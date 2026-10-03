"""Protocol conformance (A3) of the Central Control application against the
real OTA framework clients and codecs."""
import os
import socket
import stat
import threading
import time

import pytest

from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.common.errors import OtaError
from tests.unit.test_central_control import C, Transport, identity_file


class Running:
    def __init__(self, tmp_path):
        self.app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
        self.path = tmp_path / "ota.sock"
        self.server = C.MaintenanceServer(self.path, self.app)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            self.server.service(0.02)

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.server.close()


@pytest.fixture
def running(tmp_path):
    r = Running(tmp_path)
    yield r
    r.close()


def test_real_application_ipc_client_toggles_maintenance(running):
    client = ApplicationIpc(running.path)
    client.set_maintenance(True, timeout_s=2)
    assert running.app.maintenance is True
    client.set_maintenance(False, timeout_s=2)
    assert running.app.maintenance is False


def test_socket_is_owner_and_group_only(running):
    mode = stat.S_IMODE(os.stat(running.path).st_mode)
    assert mode == 0o660


def test_silent_client_does_not_block_others(running):
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.connect(str(running.path))
    silent.sendall(b'{"schema_version":1')  # partial message, never finished
    started = time.monotonic()
    ApplicationIpc(running.path).set_maintenance(True, timeout_s=2)
    assert time.monotonic() - started < 1.0
    silent.close()


def test_partial_client_is_dropped_after_deadline(running, monkeypatch):
    lingering = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lingering.connect(str(running.path))
    lingering.settimeout(4)
    assert lingering.recv(10) == b""  # closed by the 2 s per-connection deadline
    lingering.close()


def test_unknown_operation_is_rejected_by_the_real_client_contract(running):
    client = ApplicationIpc(running.path)
    with pytest.raises(OtaError) as error:
        client._call("reboot", {}, 2)
    assert error.value.code == "APPLICATION_IPC_REJECTED"


@pytest.mark.parametrize("kind", ["file", "symlink", "fifo"])
def test_server_refuses_to_replace_non_socket_path(tmp_path, kind):
    path = tmp_path / "ota.sock"
    victim = tmp_path / "victim"
    victim.write_text("keep")
    if kind == "file":
        path.write_text("keep")
    elif kind == "symlink":
        path.symlink_to(victim)
    else:
        os.mkfifo(path)
    app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
    with pytest.raises(RuntimeError):
        C.MaintenanceServer(path, app)
    assert os.path.lexists(path) and victim.read_text() == "keep"


def test_stale_socket_is_replaced(tmp_path):
    path = tmp_path / "ota.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
    server = C.MaintenanceServer(path, app)
    server.close()
    assert not os.path.lexists(path)


# --- runtime loop, wall-clock cadence, packaging (Task 3) --------------------

import statistics
import subprocess
from pathlib import Path

from capstone_ota.common.can_protocol import HeartbeatFrame, VehicleStatusFrame

ROOT = Path(__file__).parents[2]


class StampedTransport:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append((time.monotonic(), frame))


def _intervals(frames, can_id):
    stamps = [t for t, f in frames if f.can_id == can_id]
    return [b - a for a, b in zip(stamps, stamps[1:])]


def test_wall_clock_cadence_within_tolerance(tmp_path):
    transport = StampedTransport()
    app = C.CentralControl(transport, identity_file(tmp_path), heartbeat_period=0.1, vehicle_period=0.05)
    server = C.MaintenanceServer(tmp_path / "ota.sock", app)
    stop = threading.Event()
    thread = threading.Thread(target=C.run, args=(app, server, stop), daemon=True)
    thread.start()
    time.sleep(3.0)
    stop.set()
    thread.join(2)
    server.close()
    for can_id, nominal in ((0x100, 0.1), (0x200, 0.05)):
        gaps = _intervals(transport.frames, can_id)
        assert len(gaps) >= int(2.5 / nominal)
        assert abs(statistics.median(gaps) - nominal) <= 0.2 * nominal, (can_id, statistics.median(gaps))
        assert max(gaps) <= 2 * nominal, (can_id, max(gaps))
    for _, frame in transport.frames:
        (HeartbeatFrame if frame.can_id == 0x100 else VehicleStatusFrame).decode(frame)


def test_run_serves_maintenance_while_sending(tmp_path):
    transport = StampedTransport()
    app = C.CentralControl(transport, identity_file(tmp_path), heartbeat_period=0.1, vehicle_period=0.05)
    server = C.MaintenanceServer(tmp_path / "ota.sock", app)
    stop = threading.Event()
    thread = threading.Thread(target=C.run, args=(app, server, stop), daemon=True)
    thread.start()
    try:
        ApplicationIpc(tmp_path / "ota.sock").set_maintenance(True, timeout_s=2)
        time.sleep(0.3)
        assert app.maintenance is True
        assert any(f.can_id == 0x100 for _, f in transport.frames)
    finally:
        stop.set()
        thread.join(2)
        server.close()


def test_payload_script_builds_slot_payload(tmp_path):
    out = tmp_path / "payload"
    script = ROOT / "apps" / "central-control" / "make-payload.sh"
    subprocess.run([str(script), str(out)], check=True, capture_output=True)
    launcher = out / "bin" / "central-control"
    assert launcher.stat().st_mode & 0o111
    assert (out / "lib" / "central_control.py").read_bytes() == (ROOT / "apps/central-control/lib/central_control.py").read_bytes()
    assert "/opt/capstone-ota/venv/bin/python3" in launcher.read_text()
    again = subprocess.run([str(script), str(out)], capture_output=True)
    assert again.returncode != 0


def test_launcher_runs_module_help(tmp_path):
    out = tmp_path / "payload"
    subprocess.run([str(ROOT / "apps/central-control/make-payload.sh"), str(out)], check=True, capture_output=True)
    import sys
    result = subprocess.run([sys.executable, str(out / "lib" / "central_control.py"), "--help"],
                            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert result.returncode == 0 and "--heartbeat-period" in result.stdout
