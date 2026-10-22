import importlib
import json
import queue
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from capstone_ota.common.can_protocol import (ApplicationState, FunctionalTestRequestFrame,
    Gear, HeartbeatFrame, OtaCommandFrame, OtaStatus, OtaStatusFrame, Slot)
from capstone_ota.common.errors import OtaError


def api():
    return importlib.import_module("capstone_ota.coordinator.runtime")


def test_application_ipc_returns_actual_observation_and_rejects_bad_nonce(tmp_path):
    module = importlib.import_module("capstone_ota.common.application_ipc")
    path = tmp_path / "app.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen()
    requests = []
    def application():
        for bad_nonce in (False, True):
            with listener.accept()[0] as stream:
                raw = stream.makefile("rb").readline()
                request = json.loads(raw)
                requests.append(request)
                result = dict(schema_version=1, request_id="wrong" if bad_nonce else request["request_id"],
                              ok=True, result={"speed": 17, "rpm": 21, "gear": 2, "warnings": 0})
                stream.sendall(json.dumps(result).encode() + b"\n")
    thread = threading.Thread(target=application)
    thread.start()
    try:
        adapter = module.ApplicationIpc(path)
        request = FunctionalTestRequestFrame(900, 5000, Gear.DRIVE, 1, 8)
        result = adapter.observe_functional_test(request)
        assert (result.speed, result.rpm, result.gear) == (17, 21, Gear.NEUTRAL)
        with pytest.raises(OtaError):
            adapter.observe_functional_test(request)
        assert requests[0]["operation"] == "functional"
        assert requests[0]["payload"]["speed"] == 900
    finally:
        thread.join(2)
        listener.close()


def test_cache_handler_only_serves_exact_transaction_files(tmp_path):
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from urllib.error import HTTPError
    root = tmp_path / "transactions" / "550e8400" / "cluster"
    root.mkdir(parents=True)
    (root / "manifest.json").write_bytes(b"signed")
    (root / "secret.key").write_bytes(b"private")
    (root / "manifest.sig").symlink_to(root / "secret.key")
    server = ThreadingHTTPServer(("127.0.0.1", 0), api().cache_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/transactions/550e8400/cluster/manifest.json") as response:
            assert response.read() == b"signed"
        for name in ("secret.key", "manifest.sig", "../cluster/manifest.json", "manifest.json?x=1"):
            with pytest.raises(HTTPError):
                urlopen(base + "/transactions/550e8400/cluster/" + name)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class FakeBus:
    def __init__(self, *, trial=False, status=OtaStatus.IDLE):
        self.queue = queue.Queue()
        self.sent = []
        self.trial, self.status = trial, status
    def subscribe(self, ids):
        return self.queue
    def unsubscribe(self, receiver):
        pass
    def send(self, frame, timeout_s=1):
        self.sent.append(frame)
        command = OtaCommandFrame.decode(frame)
        now = __import__("time").monotonic()
        self.queue.put((now, OtaStatusFrame(self.status, command.transaction_token, Slot.A,
                                          counter=(len(self.sent)-1) % 16).encode()))
        self.queue.put((now, HeartbeatFrame("digital-cluster", (1, 0, 0), 1, 0,
            ApplicationState.TRIAL if self.trial else ApplicationState.STABLE, len(self.sent) % 256).encode()))
    def check(self):
        pass


def test_zone_discovery_requires_stable_heartbeat_and_owned_transaction_reconciliation():
    module = importlib.import_module("capstone_ota.coordinator.can_adapters")
    state = SimpleNamespace(transaction_id=None, ecu_states={}, stable_bundle={"targets": [{
        "ecu_id": "digital-cluster", "software_version": "1.0.0", "protocol_major": 1, "protocol_minor": 0}]})
    client = module.CanZoneClient(FakeBus(trial=True), lambda: state)
    with pytest.raises(OtaError):
        client.status(None, 0, .1)
    bus = FakeBus()
    client = module.CanZoneClient(bus, lambda: state)
    assert client.status(None, 0, .1).status == OtaStatus.IDLE
    state.transaction_id = "550e8400-e29b-41d4-a716-446655440000"
    state.ecu_states = {"digital-cluster": {"prepare": "intent", "trial_slot": "B"}}
    bus.status = OtaStatus.READY
    with pytest.raises(OtaError):
        client.status(None, 0, .1)


def test_runtime_recovery_precedes_mqtt_and_shutdown_preserves_state(tmp_path):
    order = []
    journal = tmp_path / "state.json"
    journal.write_text("durable")
    class Coordinator:
        state = SimpleNamespace(phase="IDLE", maintenance_enabled=False, events=[])
        def recover_on_startup(self):
            order.append("recover")
    class Mqtt:
        def start(self, receive):
            order.append("mqtt-start")
        def close(self):
            order.append("mqtt-close")
    class Server:
        def serve_forever(self):
            order.append("https-start")
        def shutdown(self):
            order.append("https-stop")
        def server_close(self):
            order.append("https-close")
    class Bus:
        def start(self): order.append("can-start")
        def close(self): order.append("can-close")
        def check(self): pass
    runtime = api().CoordinatorRuntime(Coordinator(), Mqtt(), Server(), Bus(), lambda raw: None)
    runtime.start()
    runtime.close()
    assert order.index("recover") < order.index("mqtt-start")
    assert order.index("mqtt-close") < order.index("can-close")
    assert {"https-stop", "https-close", "can-close"} <= set(order)
    assert journal.read_text() == "durable"
    assert not runtime.worker.is_alive()


def test_failed_recovery_never_subscribes():
    class Coordinator:
        state = SimpleNamespace(phase="RECOVERY_FAILED", maintenance_enabled=True)
        def recover_on_startup(self): pass
    class Resource:
        def start(self): pass
        def close(self): pass
        def serve_forever(self): pass
        def shutdown(self): pass
        def server_close(self): pass
    class Mqtt:
        def start(self, receive): pytest.fail("must not subscribe")
        def close(self): pass
    runtime = api().CoordinatorRuntime(Coordinator(), Mqtt(), Resource(), Resource(), lambda raw: None)
    with pytest.raises(OtaError):
        runtime.start()
    runtime.close()


def test_runtime_surfaces_worker_failure():
    from time import sleep
    class Coordinator:
        state = SimpleNamespace(phase="IDLE", maintenance_enabled=False, events=[])
        def recover_on_startup(self): pass
    class Resource:
        def start(self): pass
        def close(self): pass
        def check(self): pass
        def serve_forever(self): pass
        def shutdown(self): pass
        def server_close(self): pass
    class Mqtt:
        def start(self, receive): self.receive = receive
        def close(self): pass
    def fail(raw): raise RuntimeError("worker fault")
    mqtt = Mqtt()
    runtime = api().CoordinatorRuntime(Coordinator(), mqtt, Resource(), Resource(), fail)
    runtime.start()
    try:
        mqtt.receive(b"command")
        runtime.worker.join(1)
        with pytest.raises(RuntimeError, match="worker fault"):
            runtime.check()
    finally:
        runtime.close()


def test_zone_loop_heartbeats_once_per_period_without_bursting():
    from capstone_ota.agent.zonal_cli import run_loop
    clock = SimpleNamespace(value=0.0)
    stop = threading.Event()
    calls = []
    class Agent:
        def publish_heartbeat(self):
            calls.append(("heartbeat", clock.value))
        def poll_once(self, timeout):
            assert 0 <= timeout <= 0.1
            calls.append(("poll", timeout))
            clock.value += 3.5 if clock.value == 0.0 else 0.25
            if clock.value > 6:
                stop.set()
    run_loop(Agent(), stop, monotonic=lambda: clock.value)
    beats = [t for kind, t in calls if kind == "heartbeat"]
    assert beats[0] == 0.0 and beats[1] == 3.5
    assert all(b - a >= 1.0 for a, b in zip(beats[1:], beats[2:]))


def test_serve_closes_runtime_and_reports_check_failure():
    from capstone_ota.coordinator.cli import serve
    order = []
    class Runtime:
        def start(self): order.append("start")
        def check(self): raise OtaError("WORKER_STOPPED", "stopped")
        def close(self): order.append("close")
    assert serve(Runtime(), threading.Event(), interval_s=0.01) == 1
    assert order == ["start", "close"]
