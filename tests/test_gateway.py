"""Pi1 diagnostic gateway: the only route from the maintenance port's outside to Pi2's service."""
import os
import socket
import threading
import time

import pytest

from capstone_ota import debug_port
from capstone_ota.bundle import pack
from capstone_ota.debug_port import DebugPortServer, push
from capstone_ota.gateway import DiagnosticGateway
from test_bundle import keys, payload  # noqa: F401  (shared signed-payload fixtures)


@pytest.fixture
def chain(tmp_path):
    root = tmp_path / "device"
    root.mkdir()
    upstream = DebugPortServer(("127.0.0.1", 0), root)
    gateway = DiagnosticGateway(("127.0.0.1", 0), upstream.server_address, tmp_path / "gateway.jsonl",
                                connection_timeout=2.0)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (upstream, gateway)]
    for thread in threads:
        thread.start()
    yield root, gateway
    for server in (gateway, upstream):
        server.shutdown()
        server.server_close()


def _archive(tmp_path, payload, keys):
    pack(payload, tmp_path / "release", "2.0.0", 2, keys[0])
    return tmp_path / "release/app.tar.gz"


def test_gateway_relays_push_and_logs(chain, tmp_path, payload, keys):
    root, gateway = chain
    assert push("127.0.0.1", gateway.server_address[1], "A", _archive(tmp_path, payload, keys)) == "OK"
    assert (root / "slots/A/bin/app").exists()
    entry = gateway.log_tail()[-1]
    assert entry["slot"] == "A" and entry["forwarded"] is True and entry["response"] == "OK"
    assert entry["source"].startswith("127.0.0.1:") and entry["size"] > 0


def test_closed_gateway_forwards_nothing(chain, tmp_path):
    root, gateway = chain
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(1 << 20))
    gateway.close("incident")
    assert gateway.is_open is False
    assert push("127.0.0.1", gateway.server_address[1], "A", big).startswith("ERROR gateway closed")
    assert not (root / "slots").exists()
    assert gateway.log_tail()[-1]["forwarded"] is False


def test_gateway_relays_upstream_error(chain, tmp_path, payload, keys):
    _root, gateway = chain
    response = push("127.0.0.1", gateway.server_address[1], "C", _archive(tmp_path, payload, keys))
    assert response == "ERROR slot must be A or B"


def test_debug_port_default_bind_is_loopback():
    assert debug_port.build_parser().get_default("bind") == "127.0.0.1"


def test_gate_closed_mid_upload_does_not_finish_the_upload(chain, tmp_path, payload, keys):
    """An upload that started while the gate was open must not complete after it closes."""
    root, gateway = chain
    data = _archive(tmp_path, payload, keys).read_bytes()
    half = len(data) // 2
    with socket.create_connection(("127.0.0.1", gateway.server_address[1]), timeout=5) as client:
        client.sendall(b"A\n" + str(len(data)).encode() + b"\n" + data[:half])
        time.sleep(0.4)  # the gateway has now passed its open check and is streaming
        gateway.close("incident")
        client.sendall(data[half:])
        client.shutdown(socket.SHUT_WR)
        response = b""
        while chunk := client.recv(4096):
            response += chunk
    assert response.decode().startswith("ERROR gateway closed")
    assert not (root / "slots/A").exists()
