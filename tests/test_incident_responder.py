"""Pi1's incident responder: isolate, report, and restore only on a valid, single-use signed approval."""
import base64
import http.client
import json
import socketserver
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
PASS_CHECK = [sys.executable, "-m", "demo.restore_check"]  # the restored slot must be the committed one


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

    def build(order_key=None, labels=None, decision_timeout=15.0, server_url=None):
        server = None
        if server_url is None:
            server = start_server(tmp_path, served, order_key or order_keys[0], labels or {"2.0.0": "v2"})
            servers.append(server)
            server_url = f"http://127.0.0.1:{server.server_address[1]}"
        target = {"host": "local-pi2", "checkout": str(ROOT), "python": sys.executable,
                  "root": str(device.root), "public_key": str(keys[1]), "check_command": PASS_CHECK,
                  "application": "ivi"}
        responder = IncidentResponder(server_url, "PI2-001", order_keys[1],
                                      keys[1], gateway, target, decision_timeout=decision_timeout,
                                      poll_interval=0.1)
        return server, responder

    yield device, gateway, build
    for server in servers:
        server.shutdown()
        server.server_close()
    gateway.server_close()


def test_recovery_failed_reports_restores_on_approval_then_closes_gate(world):
    device, gateway, build = world
    server, responder = build()
    operate(server, "approve")
    outcome = responder.handle(dict(device.status(), **device.recovery_evidence()))
    assert gateway.is_open is False
    assert outcome["action"] == "restored" and outcome["repair"]["result"] == "rolled_back"
    assert outcome["repair"]["version"] == "2.0.0"
    incident = server.incidents[0]
    assert incident["status"] == "closed" and incident["outcome"]["result"] == "rolled_back"
    report = incident["report"]
    assert report["committed_version"] == "2.0.0" and report["gateway_state"] == "open"   # reported before the restore; it closes afterwards
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


class _Canned(socketserver.StreamRequestHandler):
    def handle(self):
        self.rfile.readline()
        self.wfile.write(self.server.reply)


@pytest.fixture
def canned_server():
    servers = []

    def start(reply: bytes):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Canned)
        server.reply, server.daemon_threads = reply, True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"
    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("reply", [
    b"this is not http\r\n\r\n",
    b"HTTP/1.0 201 Created\r\nContent-Length: 2\r\n\r\n[]",
    b"HTTP/1.0 201 Created\r\nContent-Length: 9\r\n\r\n{\"id\": 5}",
])
def test_garbage_from_the_control_centre_ends_in_maintenance_required(world, canned_server, reply):
    device, gateway, build = world
    _server, responder = build(server_url=canned_server(reply), decision_timeout=1.0)
    outcome = responder.handle(device.status())
    assert outcome["action"] == "maintenance_required" and gateway.is_open is False


def test_garbage_while_polling_for_the_decision_is_ignored(world, canned_server):
    device, _gateway, build = world
    # Reports are accepted, but every order poll gets a truncated body (IncompleteRead).
    _server, responder = build(server_url=canned_server(b"HTTP/1.0 201 Created\r\nContent-Length: 20\r\n\r\n{\"id\": \"INC-0001\"}"),
                               decision_timeout=1.0)
    assert responder.handle(device.status())["action"] == "maintenance_required"


def test_order_for_another_version_is_refused_and_not_burned(world, order_keys):
    _device, _gateway, build = world
    _server, responder = build()
    raw, signature = sign_order(make_order(version="3.0.0"), order_keys[0])
    body = {"order": base64.b64encode(raw).decode(), "signature": base64.b64encode(signature).decode()}
    with pytest.raises(ValueError, match="version"):
        responder.accept_order("INC-0001", NONCE, body, version="2.0.0")
    assert responder.accept_order("INC-0001", NONCE, body, version="3.0.0")["version"] == "3.0.0"


def test_responder_sends_the_api_token(world, tmp_path, served, keys, order_keys):
    device, gateway, _build = world
    server = IncidentServer(("127.0.0.1", 0), served, tmp_path / "tok.json", order_keys[0], {"2.0.0": "v2"},
                            api_token="tok-123")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        target = {"host": "local-pi2", "checkout": str(ROOT), "python": sys.executable, "root": str(device.root),
                  "public_key": str(keys[1]), "check_command": PASS_CHECK, "application": "ivi"}
        responder = IncidentResponder(f"http://127.0.0.1:{server.server_address[1]}", "PI2-001", order_keys[1],
                                      keys[1], gateway, target, decision_timeout=15.0, poll_interval=0.1,
                                      api_token="tok-123")
        operate(server, "approve")
        assert responder.handle(device.status())["action"] == "restored"
        assert server.incidents[0]["status"] == "closed"
    finally:
        server.shutdown()
        server.server_close()


def test_report_uses_the_device_answer_not_its_files(world):
    device, _gateway, build = world
    server, responder = build(decision_timeout=0.5)
    responder.target["root"] = "/nonexistent-pi2-root"  # Pi1 cannot read Pi2's files on a real vehicle.
    result = dict(device.status(), expected_tree_sha256="aa" * 32, observed_tree_sha256="bb" * 32,
                  error="recovery application tree hash mismatch")
    responder.handle(result)
    report = server.incidents[0]["report"]
    assert report["expected_tree_sha256"] == "aa" * 32 and report["observed_tree_sha256"] == "bb" * 32
    assert report["detail"] == "recovery application tree hash mismatch"
    assert report["committed_version"] == "2.0.0"


def test_repair_uses_its_own_recheck_not_the_updates_check():
    """The update's check (V3's trial) and the re-check after a restore are different commands."""
    responder = IncidentResponder.__new__(IncidentResponder)
    update_check, repair_check = ["/bin/update-check", "--trial"], ["/bin/repair-check", "--recheck"]
    responder.target = {"host": "pi2", "checkout": "/srv/x", "python": "/srv/x/python", "root": "/srv/root",
                        "public_key": "/srv/key.pub", "check_command": update_check,
                        "repair_check_command": repair_check, "application": "ivi"}
    remote = responder._repair_command("http://10.77.0.1:5")[-1]
    assert json.dumps(repair_check) in remote and "update-check" not in remote


def test_target_may_name_a_repair_recheck_but_it_must_be_a_command():
    from capstone_ota.relay import target_command
    base = {"host": "pi2", "checkout": "/srv/x", "python": "/srv/x/python", "root": "/srv/root",
            "public_key": "/srv/key.pub", "check_command": ["/bin/c"], "application": "ivi"}
    arguments = target_command(dict(base, repair_check_command=["/bin/r"]))
    assert "/bin/r" not in " ".join(arguments)  # the update never uses it
    for bad in ("not a list", [], [""], [1]):
        with pytest.raises(ValueError):
            target_command(dict(base, repair_check_command=bad))


def test_report_names_the_slot_the_device_says_failed_its_check(world):
    device, _gateway, build = world
    server, responder = build(decision_timeout=0.5)
    other = "B" if device.status()["active_slot"] == "A" else "A"      # the failed trial runs in one slot, the tamper is in the other
    result = dict(device.status(), expected_tree_sha256="aa" * 32, observed_tree_sha256="bb" * 32, checked_slot=other,
                  error="recovery application tree hash mismatch")
    responder.handle(result)
    assert server.incidents[0]["report"]["slot"] == other


def test_the_gate_stays_open_during_the_restore_and_closes_after_it(world):
    device, gateway, build = world
    server, responder = build()
    seen = {}
    original = responder._restore

    def spy(incident, order):
        seen["open_during_restore"] = gateway.is_open
        return original(incident, order)

    responder._restore = spy
    operate(server, "approve")
    assert responder.handle(dict(device.status(), **device.recovery_evidence()))["action"] == "restored"
    assert seen["open_during_restore"] is True and gateway.is_open is False


@pytest.mark.parametrize("decision", ["reject"])
def test_the_gate_closes_when_the_restore_is_rejected_too(world, decision):
    device, gateway, build = world
    server, responder = build()
    operate(server, decision)
    outcome = responder.handle(dict(device.status(), **device.recovery_evidence()))
    assert outcome["action"] == "maintenance_required" and gateway.is_open is False


def test_the_gate_closes_even_if_handling_raises(world):
    device, gateway, build = world
    server, responder = build()
    responder._handle_incident = lambda result: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        responder.handle({"result": "recovery_failed"})
    assert gateway.is_open is False


def _incident_events(gateway):
    return [entry["event"] for entry in gateway.log_tail(50) if entry["event"].startswith("incident_")]


def test_the_wait_for_the_control_centre_is_recorded_for_the_display(world):
    device, gateway, build = world
    server, responder = build()
    operate(server, "approve")
    responder.handle(dict(device.status(), **device.recovery_evidence()))
    assert _incident_events(gateway) == ["incident_reported", "incident_approved"]


def test_a_rejection_is_recorded_as_rejected(world):
    device, gateway, build = world
    server, responder = build()
    operate(server, "reject")
    responder.handle(device.status())
    assert _incident_events(gateway) == ["incident_reported", "incident_rejected"]


def test_no_answer_in_time_is_recorded_as_timeout(world):
    device, gateway, build = world
    _server, responder = build(decision_timeout=0.5)
    responder.handle(device.status())
    assert _incident_events(gateway) == ["incident_reported", "incident_timeout"]


def test_an_unreachable_control_centre_is_recorded_without_a_report(world):
    device, gateway, build = world
    _server, responder = build(server_url="http://127.0.0.1:9", decision_timeout=0.5)
    responder.handle(device.status())
    assert _incident_events(gateway) == ["incident_unreachable"]
