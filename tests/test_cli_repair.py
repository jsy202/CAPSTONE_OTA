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


def test_cli_update_reports_hashes_when_recovery_fails(tmp_path, served, keys):
    """Pi1 learns the hashes from Pi2's own answer, not by reading Pi2's files."""
    from test_device import serving
    from test_device_repair import VERIFIER
    root, counter = tmp_path / "device", tmp_path / "calls"
    check = json.dumps([sys.executable, "-c", VERIFIER, str(counter), str(root)])
    with serving(served) as url:
        first = _cli(root, "ivi-update", url + "/v2", "--public-key", str(keys[1]), "--check-command", check)
        assert first.returncode == 0, first.stderr
        second = _cli(root, "ivi-update", url + "/v3", "--check-command", check)
    result = json.loads(second.stdout)
    assert second.returncode == 1 and result["result"] == "recovery_failed"
    state = json.loads((root / "state.json").read_bytes())
    assert result["expected_tree_sha256"] == state["committed"]["tree_sha256"]
    assert result["observed_tree_sha256"] not in (None, result["expected_tree_sha256"])
