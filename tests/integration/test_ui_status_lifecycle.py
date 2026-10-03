"""Component/integration verification (SWE.5): real A/B slot lifecycle -> UI status.

The fake service manager stands in for systemd only. Its restart hook runs the
real status snapshot exactly where digital-cluster.service ExecStartPre would.
"""
import pytest

from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.agent.ui_status import read_previous, run_once, watch


TX = "00000000-0000-4000-8000-000000000001"


class RestartSnapshot:
    def __init__(self, root, out):
        self.root, self.out, self.at_restart = root, out, []

    def restart_and_wait_healthy(self, unit, timeout_seconds):
        assert unit == "digital-cluster.service"
        run_once(self.root, "1.0.0", self.out)
        self.at_restart.append(read_previous(self.out))
        return True


def application(tmp_path, name, version):
    tree = tmp_path / name
    (tree / "bin").mkdir(parents=True)
    entry = tree / "bin" / "digital-dash"
    entry.write_text(f"#!/bin/sh\necho {version}\n")
    entry.chmod(0o755)
    return tree


@pytest.fixture
def cluster(tmp_path):
    root, out = tmp_path / "cluster", tmp_path / "run" / "digital-cluster.json"
    out.parent.mkdir()
    application(root / "slots", "A", "1.0.0")
    services = RestartSnapshot(root, out)
    installer = ABSlotInstaller(root, services, service_unit="digital-cluster.service")
    run_once(root, "1.0.0", out)
    return installer, services, root, out, tmp_path


def shown(status):
    return status["state"], status["version"]


def test_trial_then_rollback_shows_1_0_0_1_1_1_1_0_0(cluster):
    installer, services, root, out, tmp = cluster
    assert shown(read_previous(out)) == ("stable", "1.0.0")
    installer.stage(TX, application(tmp, "incoming", "1.1.1"), "bin/digital-dash", "1.1.1")
    assert shown(run_once(root, "1.0.0", out)) == ("stable", "1.0.0")
    installer.activate_trial(TX, rollback_on_failure=False)
    assert shown(services.at_restart[-1]) == ("trial", "1.1.1")
    installer.rollback(TX)
    assert shown(services.at_restart[-1]) == ("stable", "1.0.0")
    assert [shown(s) for s in services.at_restart] == [("trial", "1.1.1"), ("stable", "1.0.0")]


def test_commit_reaches_stable_new_version_via_watch(cluster):
    installer, services, root, out, tmp = cluster
    installer.stage(TX, application(tmp, "incoming", "1.1.1"), "bin/digital-dash", "1.1.1")
    installer.activate_trial(TX, rollback_on_failure=False)
    installer.commit(TX)
    assert len(services.at_restart) == 1  # commit does not restart the application
    watch(root, "1.0.0", out, 1.0, iterations=1, sleep=lambda _: None)
    assert shown(read_previous(out)) == ("stable", "1.1.1")
