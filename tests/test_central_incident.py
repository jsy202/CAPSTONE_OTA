"""Pi1's entry point: gateway + relayed V3 update + incident response in one process."""
import json
import socket
import sys
import time

from capstone_ota import central_incident
from capstone_ota.cluster import ClusterDevice
from test_bundle import keys, payload  # noqa: F401
from test_device import serving
from test_device_repair import VERIFIER, served  # noqa: F401
from test_incident_responder import PASS_CHECK, ROOT, local_ssh, operate, start_server  # noqa: F401
from test_order import make_keypair, order_keys  # noqa: F401


def _argv(tmp_path, server_url, url, keys, order_keys, device, check, *extra):
    target = {"host": "local-pi2", "checkout": str(ROOT), "python": sys.executable, "root": str(device.root),
              "public_key": str(keys[1]), "check_command": check, "application": "ivi"}
    (tmp_path / "target.json").write_text(json.dumps(target))
    return ["--server", server_url, "--vehicle", "PI2-001", "--target", str(tmp_path / "target.json"),
            "--release-url", url + "/v3", "--release-public-key", str(keys[1]),
            "--order-public-key", str(order_keys[1]), "--listen", "127.0.0.1:0", "--upstream", "127.0.0.1:9",
            "--gateway-log", str(tmp_path / "gateway.jsonl"), "--result-out", str(tmp_path / "result.json"),
            "--decision-timeout", "20", "--poll-interval", "0.1", "--linger", "0", *extra]


def test_failed_update_with_tampered_recovery_is_reported_and_restored(tmp_path, served, keys, order_keys):
    root, counter = tmp_path / "device", tmp_path / "calls"
    verifier = [sys.executable, "-c", VERIFIER, str(counter), str(root)]
    device = ClusterDevice(root, verifier_command=verifier)
    server = start_server(tmp_path, served, order_keys[0], {"2.0.0": "v2"})
    try:
        with serving(served) as url:
            device.update(url + "/v2", public_key=keys[1])
            check = verifier  # V3's trial plants a file in the recovery slot, then reports failure
            operate(server, "approve")
            code = central_incident.main(_argv(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", url,
                                               keys, order_keys, device, check))
        assert code == 0
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["update"]["result"] == "recovery_failed"
        assert result["response"]["action"] == "restored"
        report = server.incidents[0]["report"]
        assert report["expected_tree_sha256"] and report["observed_tree_sha256"] != report["expected_tree_sha256"]
        assert result["gateway"]["open"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_healthy_update_starts_no_incident(tmp_path, served, keys, order_keys):
    root = tmp_path / "device"
    device = ClusterDevice(root)
    server = start_server(tmp_path, served, order_keys[0], {"2.0.0": "v2"})
    try:
        with serving(served) as url:
            device.update(url + "/v2", public_key=keys[1])
            code = central_incident.main(_argv(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", url,
                                               keys, order_keys, device, PASS_CHECK))
        assert code == 0 and server.incidents == []
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["response"]["action"] == "none" and result["gateway"]["open"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_gateway_stays_closed_while_lingering(tmp_path, served, keys, order_keys):
    """After an incident the open port answers 'closed' for the linger window, then the process exits."""
    root, counter = tmp_path / "device", tmp_path / "calls"
    verifier = [sys.executable, "-c", VERIFIER, str(counter), str(root)]
    device = ClusterDevice(root, verifier_command=verifier)
    server = start_server(tmp_path, served, order_keys[0], {"2.0.0": "v2"})
    try:
        with serving(served) as url:
            device.update(url + "/v2", public_key=keys[1])
            operate(server, "reject")
            started = time.monotonic()
            central_incident.main(_argv(tmp_path, f"http://127.0.0.1:{server.server_address[1]}", url,
                                        keys, order_keys, device, verifier, "--linger", "0.3"))
            assert time.monotonic() - started >= 0.3
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["response"]["action"] == "maintenance_required" and result["gateway"]["open"] is False
    finally:
        server.shutdown()
        server.server_close()
