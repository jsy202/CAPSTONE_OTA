import os
from pathlib import Path

import pytest

from capstone_ota.agent.installer import ReleaseInstaller
from capstone_ota.common.errors import OtaError


class FakeServiceManager:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def restart_and_wait_healthy(self, unit, timeout_seconds):
        self.calls.append((unit, timeout_seconds))
        return next(self.results)


def make_release(root: Path, version: str, executable=True):
    release = root / version
    entrypoint = release / "bin" / "digital-dash"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text(f"#!/bin/sh\necho {version}\n")
    entrypoint.chmod(0o755 if executable else 0o644)
    return release


def make_installer(tmp_path, results=(True,)):
    root = tmp_path / "capstone"
    (root / "releases").mkdir(parents=True)
    (root / "staging").mkdir()
    manager = FakeServiceManager(results)
    return ReleaseInstaller(root, manager), manager


@pytest.mark.parametrize("create_entrypoint,executable", [(False, False), (True, False)])
def test_install_staged_rejects_missing_or_non_executable_entrypoint(
    tmp_path, create_entrypoint, executable
):
    installer, _manager = make_installer(tmp_path)
    staging = installer.staging_dir / "job"
    staging.mkdir()
    if create_entrypoint:
        entrypoint = staging / "bin" / "digital-dash"
        entrypoint.parent.mkdir()
        entrypoint.write_text("not executable")
        entrypoint.chmod(0o755 if executable else 0o644)

    with pytest.raises(OtaError) as error:
        installer.install_staged("1.0.0", staging, "bin/digital-dash")

    assert error.value.code == "RELEASE_INVALID"
    assert staging.exists()


def test_install_staged_promotes_release_atomically(tmp_path):
    installer, _manager = make_installer(tmp_path)
    staging = make_release(installer.staging_dir, "job")

    release = installer.install_staged("1.0.0", staging, "bin/digital-dash")

    assert release == installer.releases_dir / "1.0.0"
    assert (release / "bin" / "digital-dash").is_file()
    assert not staging.exists()


def test_install_staged_rejects_existing_version_with_different_content(tmp_path):
    installer, _manager = make_installer(tmp_path)
    make_release(installer.releases_dir, "1.0.0")
    staging = make_release(installer.staging_dir, "job")
    (staging / "bin" / "digital-dash").write_text("different")
    (staging / "bin" / "digital-dash").chmod(0o755)

    with pytest.raises(OtaError) as error:
        installer.install_staged("1.0.0", staging, "bin/digital-dash")

    assert error.value.code == "RELEASE_CONFLICT"


def test_healthy_activation_updates_previous_then_current(tmp_path):
    installer, manager = make_installer(tmp_path)
    old = make_release(installer.releases_dir, "1.0.0")
    new = make_release(installer.releases_dir, "1.0.1")
    installer.current_link.symlink_to(old.relative_to(installer.install_root))

    result = installer.activate("1.0.1")

    assert installer.current_link.resolve() == new.resolve()
    assert installer.previous_link.resolve() == old.resolve()
    assert result.active_version == "1.0.1"
    assert result.rolled_back is False
    assert manager.calls == [("digital-dash.service", 15)]


def test_current_link_survives_atomic_link_replacement_failure(tmp_path, monkeypatch):
    installer, _manager = make_installer(tmp_path)
    old = make_release(installer.releases_dir, "1.0.0")
    make_release(installer.releases_dir, "1.0.1")
    installer.current_link.symlink_to(old.relative_to(installer.install_root))
    real_replace = os.replace

    def fail_current(source, target):
        if Path(target) == installer.current_link:
            raise OSError("simulated power loss")
        return real_replace(source, target)

    monkeypatch.setattr("capstone_ota.agent.installer.os.replace", fail_current)
    with pytest.raises(OtaError) as error:
        installer.activate("1.0.1")

    assert error.value.code == "ACTIVATION_FAILED"
    assert installer.current_link.resolve() == old.resolve()


def test_unhealthy_activation_restores_previous_release(tmp_path):
    installer, manager = make_installer(tmp_path, results=(False, True))
    old = make_release(installer.releases_dir, "1.0.0")
    make_release(installer.releases_dir, "1.0.1")
    installer.current_link.symlink_to(old.relative_to(installer.install_root))

    result = installer.activate("1.0.1")

    assert installer.current_link.resolve() == old.resolve()
    assert result.active_version == "1.0.0"
    assert result.rolled_back is True
    assert manager.calls == [
        ("digital-dash.service", 15),
        ("digital-dash.service", 15),
    ]


def test_manual_rollback_without_previous_release_leaves_current_unchanged(tmp_path):
    installer, _manager = make_installer(tmp_path)
    current = make_release(installer.releases_dir, "1.0.0")
    installer.current_link.symlink_to(current.relative_to(installer.install_root))

    with pytest.raises(OtaError) as error:
        installer.rollback()

    assert error.value.code == "ROLLBACK_UNAVAILABLE"
    assert installer.current_link.resolve() == current.resolve()
