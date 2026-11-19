"""demo.restore_check: the post-restore re-check that actually looks at the restored slot."""
import json
import subprocess
import sys

from capstone_ota.cluster import ClusterDevice
from test_bundle import keys, payload  # noqa: F401
from test_device_repair import served, tampered  # noqa: F401


def check(slot):
    done = subprocess.run([sys.executable, "-m", "demo.restore_check", str(slot)], capture_output=True, text=True,
                          timeout=30)
    return json.loads(done.stdout)


def test_check_fails_for_the_tampered_slot_and_passes_after_repair(tampered):
    device, url = tampered
    slot = device.root / "slots/A"
    assert check(slot)["passed"] is False  # planted file: not what was committed
    device.repair(url + "/v2")
    assert check(slot)["passed"] is True


def test_repair_with_the_real_check_ends_rolled_back(tampered):
    device, url = tampered
    real = ClusterDevice(device.root, [sys.executable, "-m", "demo.restore_check"])
    assert real.repair(url + "/v2")["result"] == "rolled_back"


def test_check_fails_when_nothing_is_committed(tmp_path):
    (tmp_path / "slots/A").mkdir(parents=True)
    assert check(tmp_path / "slots/A")["passed"] is False
