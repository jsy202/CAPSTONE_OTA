"""Pi1's incident responder: isolate, report, and restore only on a valid, single-use signed approval."""
import base64
import http.client
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from capstone_ota.gateway import DiagnosticGateway
from capstone_ota.incident_responder import IncidentResponder
from capstone_ota.incident_server import IncidentServer
from capstone_ota.order import sign_order
from test_bundle import keys, payload  # noqa: F401
from test_device_repair import served, tampered  # noqa: F401
from test_order import make_keypair, make_order, NONCE, order_keys  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PASS_CHECK = [sys.executable, "-c", "import json;print(json.dumps({'passed': True}))"]


@pytest.fixture(autouse=True)
def local_ssh(tmp_path, monkeypatch):
    """Same trick as demo/ivi_attack.py: 'ssh' runs the remote command locally."""
    shim_dir = tmp_path / "local-transport"
    shim_dir.mkdir()
    shim = shim_dir / "ssh"
    shim.write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv("/bin/sh",["sh","-c",sys.argv[-1]])\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}:{__import__('os').environ['PATH']}")


def start_server(tmp_path, served, order_key, labels):
    server = IncidentServer(("127.0.0.1", 0), served, tmp_path / "incidents.json", order_key, labels)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def operate(server, action, delay=0.0):
    """The human at the control centre: wait for a report, then click."""
    def click():
        deadline = time.time() + 20
        while time.time() < deadline and not server.incidents:
            time.sleep(0.05)
        time.sleep(delay)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("POST", f"/incidents/{server.incidents[0]['id']}/{action}")
        connection.getresponse().read()
        connection.close()
    thread = threading.Thread(target=click, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def world(tmp_path, tampered, served, keys, order_keys):
    device, _url = tampered
    gateway = DiagnosticGateway(("127.0.0.1", 0), ("127.0.0.1", 9), tmp_path / "gateway.jsonl")
    gateway.record({"event": "request", "source": "192.0.2.50:41000", "slot": "A", "size": 99,
                    "forwarded": True, "response": "OK"})
    servers = []

    def build(order_key=None, labels=None, decision_timeout=15.0):
        server = start_server(tmp_path, served, order_key or order_keys[0], labels or {"2.0.0": "v2"})
        servers.append(server)
        target = {"host": "local-pi2", "checkout": str(ROOT), "python": sys.executable,
                  "root": str(device.root), "public_key": str(keys[1]), "check_command": PASS_CHECK,
                  "application": "ivi"}
        responder = IncidentResponder(f"http://127.0.0.1:{server.server_address[1]}", "PI2-001", order_keys[1],
                                      keys[1], gateway, target, decision_timeout=decision_timeout,
                                      poll_interval=0.1)
        return server, responder

    yield device, gateway, build
    for server in servers:
        server.shutdown()
        server.server_close()
    gateway.server_close()


def test_recovery_failed_closes_gate_reports_and_restores_on_approval(world):
    device, gateway, build = world
    server, responder = build()
    operate(server, "approve")
    outcome = responder.handle(device.status())
    assert gateway.is_open is False
    assert outcome["action"] == "restored" and outcome["repair"]["result"] == "rolled_back"
    assert outcome["repair"]["version"] == "2.0.0"
    incident = server.incidents[0]
    assert incident["status"] == "closed" and incident["outcome"]["result"] == "rolled_back"
    report = incident["report"]
    assert report["committed_version"] == "2.0.0" and report["gateway_state"] == "closed"
    assert report["source"] == "192.0.2.50:41000" and report["cluster"] == "unaffected"
    assert report["expected_tree_sha256"] and report["observed_tree_sha256"] != report["expected_tree_sha256"]


def test_rejection_leaves_device_and_reports_maintenance(world):
    device, _gateway, build = world
    server, responder = build()
    state_before = (device.root / "state.json").read_bytes()
    operate(server, "reject")
    outcome = responder.handle(device.status())
    assert outcome["action"] == "maintenance_required" and outcome["repair"] is None
    assert (device.root / "state.json").read_bytes() == state_before
    assert server.incidents[0]["outcome"]["result"] == "maintenance_required"


def test_timeout_is_maintenance_required(world):
    device, gateway, build = world
    _server, responder = build(decision_timeout=0.5)
    outcome = responder.handle(device.status())
    assert outcome["action"] == "maintenance_required" and "decision" in outcome["reason"]
    assert gateway.is_open is False
    assert device.status()["result"] == "recovery_failed"


def test_forged_order_is_ignored(world, tmp_path):
    device, _gateway, build = world
    attacker_key, _ = make_keypair(tmp_path, "attacker")
    server, responder = build(order_key=attacker_key, decision_timeout=1.0)
    operate(server, "approve")
    outcome = responder.handle(device.status())
    assert outcome["action"] == "maintenance_required"
    assert device.status()["result"] == "recovery_failed"


def test_order_naming_a_release_that_is_not_the_ordered_version_is_refused(world):
    device, _gateway, build = world
    server, responder = build(labels={"2.0.0": "v3"})  # server misconfigured: label points at V3
    operate(server, "approve")
    outcome = responder.handle(device.status())
    assert outcome["action"] == "maintenance_required" and "version" in outcome["reason"]
    assert device.status()["result"] == "recovery_failed"


def test_order_is_single_use(world, order_keys):
    _device, _gateway, build = world
    _server, responder = build()
    raw, signature = sign_order(make_order(), order_keys[0])
    body = {"order": base64.b64encode(raw).decode(), "signature": base64.b64encode(signature).decode()}
    assert responder.accept_order("INC-0001", NONCE, body)["action"] == "restore"
    with pytest.raises(ValueError, match="already used"):
        responder.accept_order("INC-0001", NONCE, body)


def test_order_for_another_incident_is_refused(world, order_keys):
    _device, _gateway, build = world
    _server, responder = build()
    raw, signature = sign_order(make_order(incident="INC-0099"), order_keys[0])
    body = {"order": base64.b64encode(raw).decode(), "signature": base64.b64encode(signature).decode()}
    with pytest.raises(ValueError, match="incident"):
        responder.accept_order("INC-0001", NONCE, body)


def test_non_failure_result_does_nothing(world):
    _device, gateway, build = world
    server, responder = build()
    assert responder.handle({"result": "rolled_back"}) == {"action": "none"}
    assert gateway.is_open is True and server.incidents == []
