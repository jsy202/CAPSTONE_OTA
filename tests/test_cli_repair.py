"""capstone-ota ivi-repair / cluster-repair: how Pi1 asks Pi2 to restore the committed release."""
import json
import subprocess
import sys

from test_bundle import keys, payload  # noqa: F401  (fixtures used by `tampered`)
from test_device_repair import served, tampered  # noqa: F401

PASS_CHECK = json.dumps([sys.executable, "-c", "import json;print(json.dumps({'passed': True}))"])


def _cli(root, *arguments):
    return subprocess.run([sys.executable, "-m", "capstone_ota", "--root", str(root), *arguments],
                          capture_output=True, text=True, timeout=60)


def test_cli_repair_rolls_back(tampered):
    device, url = tampered
    process = _cli(device.root, "ivi-repair", url + "/v2", "--check-command", PASS_CHECK)
    result = json.loads(process.stdout)
    assert process.returncode == 2 and result["result"] == "rolled_back"
    assert result["version"] == "2.0.0" and result["validation"] == "external"


def test_cli_repair_rejects_wrong_release(tampered):
    device, url = tampered
    before = (device.root / "state.json").read_bytes()
    process = _cli(device.root, "ivi-repair", url + "/v3", "--check-command", PASS_CHECK)
    assert process.returncode == 1 and "OTA error:" in process.stderr
    assert (device.root / "state.json").read_bytes() == before


def test_cli_cluster_repair_is_available(tampered):
    device, url = tampered
    process = _cli(device.root, "cluster-repair", url + "/v2", "--initialization-only")
    assert json.loads(process.stdout)["result"] == "rolled_back"
