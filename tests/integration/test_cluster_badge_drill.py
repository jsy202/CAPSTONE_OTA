"""The hardware-acceptance drill drives the real A/B slot API step by step."""
import importlib.util
from pathlib import Path

import pytest

from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.agent.ui_status import read_previous, run_once

SCRIPT = Path(__file__).parents[2] / "ota" / "scripts" / "cluster_badge_drill.py"


def _drill():
    spec = importlib.util.spec_from_file_location("cluster_badge_drill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Restart:
    def __init__(self, root, out):
        self.root, self.out, self.seen = root, out, []

    def restart_and_wait_healthy(self, unit, timeout_seconds):
        run_once(self.root, "1.0.0", self.out)          # what ExecStartPre does
        status = read_previous(self.out)
        self.seen.append((status["state"], status["version"], status["restored_at"] is not None))
        return True


@pytest.fixture
def rig(tmp_path):
    root, state, out = tmp_path / "opt", tmp_path / "drill", tmp_path / "run" / "status.json"
    out.parent.mkdir()
    for name in ("slots/A", "payload"):
        entry = (root if name.startswith("slots") else tmp_path) / name / "bin" / "digital-dash"
        entry.parent.mkdir(parents=True)
        entry.write_text("#!/bin/sh\n")
        entry.chmod(0o755)
    services = Restart(root, out)
    factory = lambda: ABSlotInstaller(root, services, service_unit="digital-cluster.service")
    run = lambda *args: _drill().main(["--install-root", str(root), "--state-dir", str(state), *args],
                                      installer_factory=factory)
    return run, services, tmp_path


def test_drill_trial_then_rollback_shows_trial_then_restored(rig):
    run, services, tmp = rig
    assert run("stage", "--payload", str(tmp / "payload"), "--version", "1.1.1") == 0
    assert run("activate") == 0
    assert run("rollback") == 0
    assert services.seen == [("trial", "1.1.1", False), ("stable", "1.0.0", True)]


def test_drill_commit_finishes_transaction(rig, capsys):
    run, services, tmp = rig
    run("stage", "--payload", str(tmp / "payload"), "--version", "1.1.1")
    run("activate")
    assert run("commit") == 0
    assert run("status") == 0
    assert '"phase": "stable"' in capsys.readouterr().out


def test_drill_refuses_activate_without_stage(rig, capsys):
    run, _, _ = rig
    assert run("activate") == 2
    assert "stage first" in capsys.readouterr().err
