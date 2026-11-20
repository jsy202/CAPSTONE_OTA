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


def test_forwarded_means_the_whole_upload_was_delivered(chain):
    _root, gateway = chain
    with socket.create_connection(gateway.server_address, timeout=3) as client:
        client.sendall(b"A\n1000\n" + b"x" * 10)
        client.shutdown(socket.SHUT_WR)
        assert client.makefile().readline().startswith("ERROR")
    time.sleep(0.2)
    assert gateway.log_tail()[-1]["forwarded"] is False


def test_extra_connections_are_refused_when_busy(tmp_path):
    upstream = DebugPortServer(("127.0.0.1", 0), tmp_path)
    gateway = DiagnosticGateway(("127.0.0.1", 0), upstream.server_address, tmp_path / "g.jsonl",
                                connection_timeout=5.0, max_connections=1)
    for server in (upstream, gateway):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(gateway.server_address, timeout=3):  # holds the only slot, says nothing
            time.sleep(0.2)
            with socket.create_connection(gateway.server_address, timeout=3) as second:
                assert second.makefile().readline().strip() == "ERROR gateway busy"
    finally:
        for server in (gateway, upstream):
            server.shutdown()
            server.server_close()


def test_slow_drip_upload_hits_the_total_deadline(tmp_path):
    upstream = DebugPortServer(("127.0.0.1", 0), tmp_path)
    gateway = DiagnosticGateway(("127.0.0.1", 0), upstream.server_address, tmp_path / "g.jsonl",
                                connection_timeout=2.0, request_deadline=0.6)
    for server in (upstream, gateway):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(gateway.server_address, timeout=5) as client:
            client.sendall(b"A\n1000\n")
            reply = ""
            for _ in range(10):  # each pause is below the per-read timeout, together they are not
                try:
                    client.sendall(b"x")
                except OSError:
                    break
                time.sleep(0.25)
            reply = client.makefile().readline().strip()
        assert reply == "ERROR request too slow"
    finally:
        for server in (gateway, upstream):
            server.shutdown()
            server.server_close()


def test_an_upstream_that_answers_and_closes_early_is_not_reported_as_a_transport_error(monkeypatch):
    """Regression for a CI flake: Pi2 answers 'ERROR slot must be A or B' and closes while the body is still being
    sent; shutdown() on the already-closed upstream raises ENOTCONN (Errno 107), which must not hide that answer."""
    import errno
    import io
    import time
    from types import SimpleNamespace

    from capstone_ota import gateway as gw

    class Upstream:
        def __init__(self):
            self.replied = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def sendall(self, data):
            pass

        def shutdown(self, how):
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")

        def recv(self, size):
            if self.replied:
                return b""
            self.replied = True
            return b"ERROR slot must be A or B\n"

    monkeypatch.setattr(gw.socket, "create_connection", lambda *args, **kwargs: Upstream())
    handler = gw._Handler.__new__(gw._Handler)
    handler.rfile = io.BytesIO(b"x" * 10)
    handler.server = SimpleNamespace(upstream=("127.0.0.1", 1), connection_timeout=1.0, is_open=True)
    handler.deadline = time.monotonic() + 30
    handler.forwarded = False
    assert handler._forward("C", 10) == "ERROR slot must be A or B"
    assert handler.forwarded is False   # a refused upload was never delivered
