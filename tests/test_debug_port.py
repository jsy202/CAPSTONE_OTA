from pathlib import Path
import socket
import threading
import time

import pytest

from capstone_ota.bundle import pack
from capstone_ota.debug_port import DebugPortServer, push, slot_path
from test_bundle import keys, payload  # Shared real signed-payload fixtures.


@pytest.fixture
def running_port(tmp_path):
    root = tmp_path / "device"
    (root / "slots" / "A").mkdir(parents=True)
    (root / "slots" / "A" / "marker.txt").write_text("original")
    (root / "state.json").write_text("do-not-touch")
    (root / "trusted.pub").write_text("do-not-touch")
    server = DebugPortServer(("127.0.0.1", 0), root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_slot_path_rejects_anything_outside_slots(tmp_path):
    root = tmp_path / "device"
    (root / "slots").mkdir(parents=True)
    for bad in ("C", "../state.json", "A/../../state.json", "", "state.json"):
        with pytest.raises(ValueError):
            slot_path(root, bad)
    assert slot_path(root, "A") == (root / "slots" / "A")


def test_push_replaces_the_named_slot_and_nothing_else(running_port, payload, keys, tmp_path):
    root, port = running_port
    release = tmp_path / "release"
    pack(payload, release, "9.9.9", 9, keys[0])
    reply = push("127.0.0.1", port, "A", release / "app.tar.gz")
    assert reply == "OK"
    assert (root / "slots/A/bin/app").is_file()
    assert not (root / "slots/A/marker.txt").exists()  # old slot contents fully replaced
    assert (root / "state.json").read_text() == "do-not-touch"
    assert (root / "trusted.pub").read_text() == "do-not-touch"


def test_push_rejects_an_invalid_slot_name_without_touching_anything(running_port, payload, keys, tmp_path):
    root, port = running_port
    release = tmp_path / "release"
    pack(payload, release, "9.9.9", 9, keys[0])
    reply = push("127.0.0.1", port, "C", release / "app.tar.gz")
    assert reply.startswith("ERROR")
    assert (root / "slots/A/marker.txt").read_text() == "original"
    assert (root / "state.json").read_text() == "do-not-touch"


def test_a_stalled_client_cannot_hold_the_handler_thread_forever(tmp_path):
    """An always-open, one-thread-per-connection port must bound a silent/stalled
    client, or enough of them starve the port of threads for real clients."""
    root = tmp_path / "device"
    (root / "slots" / "A").mkdir(parents=True)
    server = DebugPortServer(("127.0.0.1", 0), root, connection_timeout=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as stalled:
            stalled.sendall(b"A\n")  # slot name only; never sends the size line
            started = time.monotonic()
            reply = stalled.recv(4096)  # the server's own timeout must close this, not our 5s one
            assert time.monotonic() - started < 2
            assert reply == b"ERROR timed out\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
