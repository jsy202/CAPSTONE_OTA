from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from capstone_ota.bundle import pack
from test_bundle import keys, payload  # Shared real signed-payload fixtures.


@contextmanager
def serving(directory):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def device_type():
    assert importlib.util.find_spec("capstone_ota.device") is not None, "single-device state machine is missing"
    return importlib.import_module("capstone_ota.device").Device


@pytest.fixture
def releases(tmp_path, payload, keys):
    served = tmp_path / "served"
    pack(payload, served / "v1", "1.0.0", 1, keys[0])
    pack(payload, served / "v2", "1.1.0", 2, keys[0])
    pack(payload, served / "v3", "1.2.0", 3, keys[0])
    (payload / "bin/app").write_text("#!/bin/sh\nexit 1\n")
    pack(payload, served / "bad", "1.3.0", 4, keys[0])
    return served


def test_real_http_install_alternates_slots_and_pins_key(device_type, releases, keys, tmp_path):
    root = tmp_path / "device"
    device = device_type(root)
    with serving(releases) as url:
        first = device.update(url + "/v1", public_key=keys[1])
        assert first["result"] == "committed"
        assert root.joinpath("current").readlink() == Path("slots/A")
        assert device.status()["version"] == "1.0.0"
        assert root.joinpath("trusted.pub").read_bytes() == keys[1].read_bytes()
        device.update(url + "/v2")
        assert root.joinpath("current").readlink() == Path("slots/B")
        assert device.status()["min_svn"] == 2
        device.update(url + "/v3")
        assert root.joinpath("current").readlink() == Path("slots/A")
        assert device.status()["version"] == "1.2.0"


def test_failed_trial_restores_previous_app_without_raising_floor(device_type, releases, keys, tmp_path):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v2", public_key=keys[1])
        result = device.update(url + "/bad")
    assert result["result"] == "rolled_back"
    assert result["version"] == "1.1.0"
    assert result["min_svn"] == 2
    assert result["active_slot"] == "A"
    assert subprocess.run([str(device.root / "current/bin/app"), "--check"]).returncode == 0


def test_corrupt_archive_never_changes_current(device_type, releases, keys, tmp_path):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        archive = releases / "v2/app.tar.gz"
        archive.write_bytes(archive.read_bytes() + b"modified")
        with pytest.raises(ValueError, match="hash"):
            device.update(url + "/v2")
    assert device.status()["version"] == "1.0.0"
    assert device.status()["active_slot"] == "A"


def test_low_svn_is_rejected_even_with_higher_product_version(device_type, releases, keys, payload, tmp_path):
    (payload / "bin/app").write_text("#!/bin/sh\nexit 0\n")
    pack(payload, releases / "old-security", "9.0.0", 1, keys[0])
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v2", public_key=keys[1])
        with pytest.raises(ValueError, match="security version"):
            device.update(url + "/old-security")
    assert device.status()["version"] == "1.1.0"


def test_same_or_older_product_version_is_not_reinstalled(device_type, releases, keys, tmp_path):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        with pytest.raises(ValueError, match="newer"):
            device.update(url + "/v1")
    assert device.status()["active_slot"] == "A"


class SimulatedPowerLoss(BaseException):
    pass


def test_interrupted_trial_is_rolled_back_by_next_invocation(device_type, releases, keys, tmp_path, monkeypatch):
    root = tmp_path / "device"
    device = device_type(root)
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        with monkeypatch.context() as patch:
            patch.setattr(device, "_check", lambda *_args: (_ for _ in ()).throw(SimulatedPowerLoss()))
            with pytest.raises(SimulatedPowerLoss):
                device.update(url + "/v2")
        assert device_type(root).status()["phase"] == "trial"
        assert device_type(root).status()["active_slot"] == "B"
        result = device_type(root).update()
    assert result["result"] == "rolled_back"
    assert result["version"] == "1.0.0"
    assert result["active_slot"] == "A"
    assert result["min_svn"] == 1


def test_recovery_refuses_tampered_previous_slot(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        with monkeypatch.context() as patch:
            patch.setattr(device, "_check", lambda *_args: (_ for _ in ()).throw(SimulatedPowerLoss()))
            with pytest.raises(SimulatedPowerLoss):
                device.update(url + "/v2")
        (device.root / "slots/A/config.json").write_text("tampered")
        with pytest.raises(ValueError, match="recovery"):
            device_type(device.root).update()
    assert device.status()["phase"] == "trial"
    assert device.status()["min_svn"] == 1


def test_status_is_read_only_and_rejects_corrupted_journal(device_type, tmp_path):
    root = tmp_path / "device"
    assert device_type(root).status()["result"] == "empty"
    assert not root.exists()
    root.mkdir()
    (root / "state.json").write_text('{"garbage":true}')
    before = (root / "state.json").read_bytes()
    with pytest.raises(ValueError, match="state"):
        device_type(root).status()
    assert (root / "state.json").read_bytes() == before


def test_pinned_key_cannot_be_replaced(device_type, releases, keys, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other = tmp_path / "other.pub"
    other.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        with pytest.raises(ValueError, match="pinned"):
            device.update(url + "/v2", public_key=other)
    assert device.status()["version"] == "1.0.0"


def test_concurrent_update_is_rejected(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    original = device._check
    def check(slot):
        with pytest.raises(ValueError, match="busy"):
            device_type(device.root).update()
        return original(slot)
    monkeypatch.setattr(device, "_check", check)
    with serving(releases) as url:
        assert device.update(url + "/v1", public_key=keys[1])["result"] == "committed"


def test_missing_state_never_reinitializes_an_existing_device(device_type, releases, keys, tmp_path):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v2", public_key=keys[1])
        (device.root / "state.json").unlink()
        with pytest.raises(ValueError, match="state"):
            device.update(url + "/v1", public_key=keys[1])


def test_device_root_and_slot_symlinks_are_rejected(device_type, releases, keys, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        device_type(link).update()
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        (device.root / "slots/B").symlink_to(real, target_is_directory=True)
        with pytest.raises(ValueError, match="slot"):
            device.update(url + "/v2")
    assert list(real.iterdir()) == []


def test_cli_installs_and_reports_status(releases, keys, tmp_path):
    command = [sys.executable, "-m", "capstone_ota", "--root", str(tmp_path / "device")]
    with serving(releases) as url:
        result = subprocess.run(command + ["update", url + "/v1", "--public-key", str(keys[1])],
                                capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(command + ["status"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["version"] == "1.0.0"


def test_cli_failed_trial_returns_nonzero_but_reports_successful_recovery(releases, keys, tmp_path):
    command = [sys.executable, "-m", "capstone_ota", "--root", str(tmp_path / "device")]
    with serving(releases) as url:
        first = subprocess.run(command + ["update", url + "/v1", "--public-key", str(keys[1])],
                               capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        bad = subprocess.run(command + ["update", url + "/bad"], capture_output=True, text=True)
    assert bad.returncode == 2, bad.stderr
    assert json.loads(bad.stdout)["result"] == "rolled_back"
    assert json.loads(bad.stdout)["version"] == "1.0.0"


def test_error_after_durable_commit_never_rolls_back_the_security_floor(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        save = device._save
        def error_after_write(state):
            save(state)
            if state["result"] == "committed" and state["min_svn"] == 2:
                raise OSError("directory flush reported an error after state replacement")
        monkeypatch.setattr(device, "_save", error_after_write)
        device.update(url + "/v2")
    assert device.status()["min_svn"] == 2
    assert device.status()["version"] == "1.1.0"
    assert device.status()["active_slot"] == "B"


def test_failed_first_install_does_not_claim_successful_recovery(device_type, releases, keys, tmp_path):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        result = device.update(url + "/bad", public_key=keys[1])
    assert result["result"] == "failed"
    assert result["active_slot"] is None
    assert result["min_svn"] == 0


def test_unhealthy_previous_app_leaves_recovery_pending(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        monkeypatch.setattr(device, "_check", lambda _slot: False)
        with pytest.raises(ValueError, match="recovery failed"):
            device.update(url + "/v2")
    assert device.status()["result"] == "recovery_failed"
    assert device.status()["phase"] == "trial"


@pytest.mark.parametrize("after_select", [False, True])
def test_crash_at_selector_boundary_recovers_committed_slot(device_type, releases, keys, tmp_path, monkeypatch, after_select):
    device = device_type(tmp_path / "device")
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1])
        select = device._select
        def interrupted(slot):
            if after_select:
                select(slot)
            raise SimulatedPowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(device, "_select", interrupted)
            with pytest.raises(SimulatedPowerLoss):
                device.update(url + "/v2")
        result = device_type(device.root).update()
    assert result["active_slot"] == "A"
    assert result["version"] == "1.0.0"
    assert result["min_svn"] == 1


def test_optional_systemd_mode_controls_only_fixed_service(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    actions = []
    monkeypatch.setattr(device, "_service", lambda action: actions.append(action) or True)
    with serving(releases) as url:
        device.update(url + "/v1", public_key=keys[1], systemd=True)
    assert actions[:2] == ["stop", "restart"]
    assert actions[2:] == ["is-active"] * 5
    assert device.status()["result"] == "committed"


def test_first_install_interrupted_during_staging_can_be_retried(device_type, releases, keys, tmp_path, monkeypatch):
    device = device_type(tmp_path / "device")
    module = importlib.import_module("capstone_ota.device")
    rename = module.os.replace
    with serving(releases) as url:
        def crash_after_stage(source, target):
            rename(source, target)
            if Path(target) == device.root / "slots/A":
                raise SimulatedPowerLoss()
        with monkeypatch.context() as patch:
            patch.setattr(module.os, "replace", crash_after_stage)
            with pytest.raises(SimulatedPowerLoss):
                device.update(url + "/v1", public_key=keys[1])
        assert device_type(device.root).update(url + "/v1")["result"] == "committed"
