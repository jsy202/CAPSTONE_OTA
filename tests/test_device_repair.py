"""Device.repair(): the only exit from recovery_failed, and only back to the exact committed image."""
import json
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from capstone_ota import device as device_module
from capstone_ota.bundle import pack, tree_hash
from capstone_ota.cluster import ClusterDevice
from test_bundle import keys, payload  # Shared real signed-payload fixtures.
from test_device import serving  # Shared local-HTTP release server.

# Call 0 blesses the V2 baseline, call 1 is V3's trial: it plants a file in the recovery
# slot (stand-in for the open maintenance port) and reports failure; later calls pass.
VERIFIER = (
    "import sys, json, pathlib\n"
    "counter = pathlib.Path(sys.argv[1])\n"
    "n = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(n + 1))\n"
    "if n == 1:\n"
    "    (pathlib.Path(sys.argv[2]) / 'slots/A/planted.txt').write_text('planted')\n"
    "print(json.dumps({'passed': n != 1}))\n"
)


def _other_key(tmp_path):
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "other.key"
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    return key


@pytest.fixture
def served(tmp_path, payload, keys):
    root = tmp_path / "served"
    pack(payload, root / "v2", "2.0.0", 2, keys[0])
    pack(payload, root / "v3", "3.0.0", 2, keys[0])
    # Same version and svn as V2 and validly signed, but different content.
    other = tmp_path / "other-payload"
    (other / "bin").mkdir(parents=True)
    (other / "bin/app").write_text("#!/bin/sh\nexit 0\n")
    (other / "bin/app").chmod(0o755)
    (other / "extra.txt").write_text("not the committed image")
    pack(other, root / "v2-other", "2.0.0", 2, keys[0])
    pack(payload, root / "v2-forged", "2.0.0", 2, _other_key(tmp_path))
    return root


@pytest.fixture
def tampered(tmp_path, served, keys):
    """V2 committed in slot A, V3 trial failed, recovery slot tampered -> recovery_failed."""
    root = tmp_path / "device"
    counter = tmp_path / "calls"
    device = ClusterDevice(root, verifier_command=[sys.executable, "-c", VERIFIER, str(counter), str(root)])
    with serving(served) as url:
        device.update(url + "/v2", public_key=keys[1])
        with pytest.raises(ValueError, match="recovery failed"):
            device.update(url + "/v3")
        assert device.status()["result"] == "recovery_failed"
        yield device, url


def test_repair_restores_committed_release(tampered):
    device, url = tampered
    result = device.repair(url + "/v2")
    assert result["result"] == "rolled_back" and result["version"] == "2.0.0"
    assert result["phase"] == "stable" and result["active_slot"] == "A"
    assert not (device.root / "slots/A/planted.txt").exists()


def _unchanged(device, action):
    state_before = (device.root / "state.json").read_bytes()
    tree_before = tree_hash(device.root / "slots/A")
    with pytest.raises(ValueError) as caught:
        action()
    assert (device.root / "state.json").read_bytes() == state_before
    assert tree_hash(device.root / "slots/A") == tree_before
    return str(caught.value)


def test_repair_rejects_other_version(tampered):
    device, url = tampered
    _unchanged(device, lambda: device.repair(url + "/v3"))


def test_repair_rejects_same_version_other_content(tampered):
    device, url = tampered
    message = _unchanged(device, lambda: device.repair(url + "/v2-other"))
    assert "committed release" in message


def test_repair_rejects_bad_signature(tampered):
    device, url = tampered
    message = _unchanged(device, lambda: device.repair(url + "/v2-forged"))
    assert "signature" in message


def test_repair_refuses_when_not_recovery_failed(tmp_path, served, keys):
    device = ClusterDevice(tmp_path / "healthy")
    with serving(served) as url:
        device.update(url + "/v2", public_key=keys[1])
        with pytest.raises(ValueError, match="only for a recovery_failed device"):
            device.repair(url + "/v2")


def test_repair_is_retryable_after_interrupted_install(tampered, monkeypatch):
    device, url = tampered
    real_replace = device_module.os.replace
    calls = []

    def flaky(source, destination):
        if str(destination).endswith("slots/A") and not calls:
            calls.append(destination)
            raise OSError("power lost during slot swap")
        return real_replace(source, destination)

    monkeypatch.setattr(device_module.os, "replace", flaky)
    with pytest.raises(OSError):
        device.repair(url + "/v2")
    assert json.loads((device.root / "state.json").read_bytes())["result"] == "recovery_failed"
    assert device.repair(url + "/v2")["result"] == "rolled_back"


def test_recovery_failed_progress_records_observed_hash(tampered):
    device, _url = tampered
    progress = json.loads((device.root / "progress.json").read_bytes())
    state = json.loads((device.root / "state.json").read_bytes())
    assert progress["expected_tree_sha256"] == state["committed"]["tree_sha256"]
    assert progress["observed_tree_sha256"] != progress["expected_tree_sha256"]
