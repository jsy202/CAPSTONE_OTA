"""Hardware-free checks of three separate bus endpoints; not physical CAN tests."""
import importlib
import queue
import sys
import threading
import time

import pytest

from test_virtual_compatibility import make_slot


def bench():
    return importlib.import_module("demo.can_bench")


class LocalBus:
    def __init__(self, peers):
        self.inbox = queue.Queue()
        self.peers = peers
        peers.append(self)

    def send(self, identifier, data):
        for peer in self.peers:
            if peer is not self:
                peer.inbox.put((identifier, data))

    def receive(self, deadline):
        try:
            return self.inbox.get(timeout=max(0.001, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("CAN receive timeout")


def test_data_id_change_keeps_can_id_and_length_but_breaks_receiver_validation():
    b = bench()
    good = b.protected_frame(400, 250, 0x21)
    incompatible = b.protected_frame(400, 250, 0x22)
    assert good["id"] == incompatible["id"] == 0x200
    assert len(bytes.fromhex(good["data"])) == len(bytes.fromhex(incompatible["data"])) == 8
    assert b.decode_protected(good)["speed"] == 400
    with pytest.raises(ValueError, match="Data ID/CRC"):
        b.decode_protected(incompatible)


@pytest.mark.parametrize("data_id,passed", [(0x21, True), (0x22, False)])
def test_three_bus_participants_compare_input_with_cluster_consumption(tmp_path, data_id, passed):
    b = bench()
    peers = []
    central, cluster, verifier = [LocalBus(peers) for _ in range(3)]
    payload = make_slot(tmp_path)
    (payload / "data-id.txt").write_text(str(data_id))
    (tmp_path / "current").symlink_to(payload)
    stop = threading.Event()

    def receive():
        while not stop.is_set():
            try:
                identifier, data = cluster.receive(time.monotonic() + 0.05)
                b.handle_cluster_frame(state, cluster, identifier, data)
            except TimeoutError:
                pass

    state = {"nonce": None, "counter": None, "speed": None}
    worker = threading.Thread(target=receive)
    worker.start()
    try:
        def source(speeds):
            return b.send_profile(central, tmp_path, speeds)
        report = b.verify_bus(verifier, source, timeout=1)
        assert report["passed"] is passed
        assert report["frames_seen"] == 6
        assert report["accepted"] == (6 if passed else 0)
        assert report["error"] == (None if passed else "E2E_CONFIGURATION_INCOMPATIBLE")
    finally:
        stop.set()
        worker.join(timeout=1)


def test_stale_cluster_ack_cannot_start_verification():
    b = bench()
    peers = []
    verifier, other = LocalBus(peers), LocalBus(peers)
    other.send(0x611, b"old!\xff\x00\x00\x02")
    called = []
    report = b.verify_bus(verifier, lambda speeds: called.append(speeds), timeout=0.05)
    assert report["passed"] is False
    assert not called


def test_silent_cluster_cannot_pass_even_with_valid_observed_frames():
    b = bench()
    peers = []
    verifier, central = LocalBus(peers), LocalBus(peers)
    def source(speeds):
        for i, speed in enumerate(speeds):
            frame = b.protected_frame(speed, i, 0x21)
            central.send(frame["id"], bytes.fromhex(frame["data"]))
        return {"sent": len(speeds)}
    # Respond to start, but never report any consumed values.
    def acknowledge():
        _identifier, start = central.receive(time.monotonic() + 1)
        central.send(0x611, start[:4] + b"\xff\x00\x00\x02")
    worker = threading.Thread(target=acknowledge)
    worker.start()
    report = b.verify_bus(verifier, source, timeout=0.1)
    worker.join(timeout=1)
    assert report["passed"] is False


def test_trailing_extra_frame_and_report_cannot_be_ignored():
    b = bench()
    peers = []
    verifier, sender = LocalBus(peers), LocalBus(peers)
    challenge = {}

    def acknowledge():
        _identifier, start = sender.receive(time.monotonic() + 1)
        challenge["nonce"] = start[:4]
        sender.send(0x611, start[:4] + b"\xff\x00\x00\x02")

    def source(speeds):
        for i, speed in enumerate([*speeds, speeds[-1]]):
            frame = b.protected_frame(speed, i)
            sender.send(0x200, bytes.fromhex(frame["data"]))
            sender.send(0x611, challenge["nonce"] + bytes([i]) + speed.to_bytes(2, "big") + b"\x01")
        return {"sent": len(speeds)}

    worker = threading.Thread(target=acknowledge)
    worker.start()
    report = b.verify_bus(verifier, source, timeout=1)
    worker.join(timeout=1)
    assert report["passed"] is False
    assert "extra" in report["error"]


@pytest.mark.parametrize("body,approved", [('print(\'{"passed":false}\')', False), ('print("{}")', False),
                                          ('print(\'{"passed":true}\');raise SystemExit(1)', False),
                                          ('print(\'{"passed":true}\')', True)])
def test_remote_gate_requires_successful_explicit_verdict(tmp_path, body, approved):
    b = bench()
    slot = make_slot(tmp_path)
    device = b.RemoteDevice(tmp_path / "device", [sys.executable, "-c", body])
    target = device.root / "slots/A"
    target.parent.mkdir(parents=True)
    slot.rename(target)
    assert device._check("A") is approved
