"""End to end: a recall-left-open debug port swaps the recovery slot WHILE V3 is still
on trial (mid external verification), a confirmed anomaly (standing in for Pi3's real
CPU-contention detection) then forces a rollback, and the pinned tree-hash check must
catch the swap before the tampered recovery slot is ever activated -- with a toggle
showing the same attack succeed when that check is disabled.
"""
import sys
import threading

import pytest

from capstone_ota.bundle import pack
from capstone_ota.cluster import ClusterDevice
from capstone_ota.debug_port import DebugPortServer
from test_bundle import keys, payload  # Shared real signed-payload fixtures.
from test_device import serving  # Shared local-HTTP release server.

# Simulates Pi3's verdict on the external-verifier slot: call 0 blesses the V2 baseline
# install, call 1 is V3's trial -- exactly there, while "Pi3" is mid-evaluation, it
# fires the debug-port attack against the recovery slot before reporting the confirmed
# anomaly, matching the real timeline (the swap happens during V3's trial window, not
# after). Call 2 (vulnerable mode only) re-blesses whatever is now in the recovery slot.
VERIFIER_SCRIPT = (
    "import sys, json, pathlib\n"
    "counter = pathlib.Path(sys.argv[1])\n"
    "n = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(n + 1))\n"
    "if n == 1:\n"
    "    from capstone_ota.debug_port import push\n"
    "    push(sys.argv[2], int(sys.argv[3]), 'A', sys.argv[4])\n"
    "print(json.dumps({'passed': n != 1}))\n"
)


@pytest.fixture
def two_releases(tmp_path, payload, keys):
    served = tmp_path / "served"
    pack(payload, served / "v2", "2.0.0", 2, keys[0])
    pack(payload, served / "v3", "3.0.0", 2, keys[0])
    return served


def _planted_archive(tmp_path, keys):
    """A different, but still validly-packed and signed, release -- the "some other
    image" an attacker plants through the open port. Its own self-check still passes
    (exit 0); only its tree contents differ from what was actually committed."""
    planted_payload = tmp_path / "planted-payload"
    (planted_payload / "bin").mkdir(parents=True)
    (planted_payload / "bin/app").write_text("#!/bin/sh\nexit 0\n")
    (planted_payload / "bin/app").chmod(0o755)
    (planted_payload / "planted.txt").write_text("delivered via the open recall port")
    archive = tmp_path / "planted-release"
    pack(planted_payload, archive, "2.0.0", 2, keys[0])
    return archive / "app.tar.gz"


def _run(tmp_path, two_releases, keys, recovery_verification):
    device_root = tmp_path / "device"
    counter = tmp_path / "verifier-calls"
    port = DebugPortServer(("127.0.0.1", 0), device_root)
    port_thread = threading.Thread(target=port.serve_forever, daemon=True)
    port_thread.start()
    try:
        planted = _planted_archive(tmp_path, keys)
        verifier_command = [sys.executable, "-c", VERIFIER_SCRIPT, str(counter),
                            "127.0.0.1", str(port.server_address[1]), str(planted)]
        device = ClusterDevice(device_root, verifier_command=verifier_command,
                               recovery_verification=recovery_verification)
        with serving(two_releases) as url:
            device.update(url + "/v2", public_key=keys[1])  # commits V2 into slot A, untouched
            result = {}
            try:
                device.update(url + "/v3")
                result["raised"] = None
            except ValueError as exc:
                result["raised"] = str(exc)
                result["cause"] = str(exc.__cause__) if exc.__cause__ else None
    finally:
        port.shutdown()
        port.server_close()
        port_thread.join()

    return {"raised": result["raised"], "cause": result.get("cause"),
            "verifier_calls": int(counter.read_text()) if counter.exists() else 0,
            "active_slot_has_plant": (device_root / "slots/A/planted.txt").exists(),
            "current_slot": (device_root / "current").readlink().name}


def test_defended_rollback_blocks_the_planted_recovery_image(tmp_path, two_releases, keys):
    outcome = _run(tmp_path, two_releases, keys, recovery_verification=True)
    assert outcome["raised"] and "recovery failed" in outcome["raised"]
    assert outcome["cause"] and "tree hash mismatch" in outcome["cause"]
    # Pi3 blessed the V2 baseline, then flagged the V3 trial; never asked to bless the
    # swapped recovery slot, because _verify_record rejected it first.
    assert outcome["verifier_calls"] == 2
    # The attack did land on disk (the open port can't be stopped from writing) --
    # the defense is that `current` was never switched to it.
    assert outcome["active_slot_has_plant"] is True
    assert outcome["current_slot"] == "B"  # still pointed at the (now-stuck) V3 trial slot, not the plant


def test_vulnerable_mode_lets_the_planted_image_through(tmp_path, two_releases, keys):
    outcome = _run(tmp_path, two_releases, keys, recovery_verification=False)
    assert outcome["raised"] is None
    assert outcome["verifier_calls"] == 3  # also asked to bless the recovered (planted) slot, and said yes
    assert outcome["current_slot"] == "A"
    assert outcome["active_slot_has_plant"] is True
