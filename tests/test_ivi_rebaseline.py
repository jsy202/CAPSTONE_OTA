"""bench.ivi_rebaseline: build the 2.1.0 baseline releases and install them on Pi2 (no board needed)."""
import json
import subprocess
import urllib.request

import pytest

from bench import incident_field as field
from bench import ivi_rebaseline as rb
from bench.ivi_release import make
from capstone_ota.bundle import verify

QUIET = {"log": lambda *_: None}


@pytest.fixture()
def old_dir(tmp_path):
    make(tmp_path / "old")
    return tmp_path / "old"


@pytest.fixture()
def new_dir(old_dir, tmp_path):
    rb.build_releases(old_dir, tmp_path / "new")
    return tmp_path / "new"


def test_build_creates_signed_2_1_0_and_3_0_0_with_the_existing_key(old_dir, new_dir):
    public = new_dir / "keys/ivi-public.pem"
    assert public.read_bytes() == (old_dir / "keys/ivi-public.pem").read_bytes()
    for name, version in (("v2", "2.1.0"), ("v3", "3.0.0")):
        folder = new_dir / "releases" / name
        meta = verify((folder / "release.json").read_bytes(), (folder / "release.sig").read_bytes(), public)
        assert meta["version"] == version
    assert json.loads((new_dir / "releases/v2/release.json").read_text())["svn"] == 1
    assert json.loads((new_dir / "releases/v3/release.json").read_text())["svn"] >= 1


def test_build_never_copies_the_private_key_and_keeps_the_orchestrator_layout(old_dir, new_dir):
    private = new_dir / "keys/ivi-private.pem"  # incident_field reads this path to sign the attack image
    assert private.is_symlink() and private.resolve() == (old_dir / "keys/ivi-private.pem").resolve()
    for archive in (new_dir / "releases").glob("*/app.tar.gz"):
        assert b"PRIVATE" not in archive.read_bytes()


def test_build_refuses_an_existing_output_dir_and_leaves_the_old_releases_alone(old_dir, tmp_path):
    before = (old_dir / "releases/v2/release.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        rb.build_releases(old_dir, old_dir)
    (tmp_path / "taken").mkdir()
    with pytest.raises(ValueError, match="exists"):
        rb.build_releases(old_dir, tmp_path / "taken")
    assert (old_dir / "releases/v2/release.json").read_bytes() == before


class FakeBoard:
    """Pi2 stand-in: `status` answers `status`, and flips to 2.1.0 once the update ran."""

    def __init__(self, status):
        self.status, self.commands, self.updated = status, [], False

    def run(self, command, **kwargs):
        self.commands.append(command)
        if command.endswith(" status"):
            out = {"version": "2.1.0", "phase": "stable"} if self.updated else self.status
            return subprocess.CompletedProcess(command, 0, json.dumps(out), "")
        if "ivi-update http" in command:
            self.updated = True
            return subprocess.CompletedProcess(command, 0, '{"phase": "stable", "result": "committed"}', "")
        return subprocess.CompletedProcess(command, 0, '{"phase": "stable"}', "")


def test_update_command_shape():
    command = rb.update_command("http://172.20.10.14:5555/v2")
    assert command.startswith(f"cd {field.RUNTIME} && ./root-python -m capstone_ota --root {field.IVI_ROOT} ivi-update ")
    assert "http://172.20.10.14:5555/v2" in command and "--check-command" in command
    assert "--public-key" not in command  # the pinned trusted.pub is used


def test_install_refuses_when_pi2_is_not_stable(new_dir):
    board = FakeBoard({"version": "2.0.0", "phase": "recovery_failed"})
    with pytest.raises(SystemExit, match="stable"):
        rb.install_baseline({"laptop_ip": "127.0.0.1"}, {"pi2": board}, new_dir, **QUIET)
    assert not any("ivi-update http" in c for c in board.commands)


def test_install_serves_v2_runs_the_update_verifies_and_shuts_the_server_down(new_dir):
    seen = {}

    class Board(FakeBoard):
        def run(self, command, **kwargs):
            if "ivi-update http" in command:
                seen["url"] = next(p for p in command.split() if p.startswith("http"))
                seen["release"] = urllib.request.urlopen(seen["url"] + "/release.json", timeout=5).read()
            return super().run(command, **kwargs)

    lines = []
    summary = rb.install_baseline({"laptop_ip": "127.0.0.1"}, {"pi2": Board({"version": "2.0.0", "phase": "stable"})},
                                  new_dir, log=lines.append)
    assert json.loads(seen["release"])["version"] == "2.1.0"
    assert summary["version"] == "2.1.0" and summary["phase"] == "stable"
    assert lines  # it said what it was about to do
    with pytest.raises(OSError):
        urllib.request.urlopen(seen["url"] + "/release.json", timeout=2)  # the server is down again


def test_install_fails_loudly_and_stops_the_server_when_the_device_does_not_end_at_2_1_0(new_dir):
    class Stuck(FakeBoard):
        def run(self, command, **kwargs):
            if command.endswith(" status"):
                return subprocess.CompletedProcess(command, 0, json.dumps({"version": "2.0.0", "phase": "stable"}), "")
            return super().run(command, **kwargs)
    with pytest.raises(RuntimeError, match="2.1.0"):
        rb.install_baseline({"laptop_ip": "127.0.0.1"}, {"pi2": Stuck({})}, new_dir, **QUIET)


def test_install_does_nothing_when_already_at_the_baseline(new_dir):
    board = FakeBoard({"version": "2.1.0", "phase": "stable"})
    summary = rb.install_baseline({"laptop_ip": "127.0.0.1"}, {"pi2": board}, new_dir, **QUIET)
    assert summary["skipped"] is True
    assert not any("ivi-update http" in c for c in board.commands)
