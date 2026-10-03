"""Unit verification (SWE.4) of the Cluster UI status decision and slot reader."""
import os
from dataclasses import replace

import pytest

from capstone_ota.agent.slots import ABSlotInstaller, SlotState
from capstone_ota.agent.ui_status import SCHEMA_VERSION, SlotSnapshot, decide, read_snapshot


TX = "00000000-0000-4000-8000-000000000001"
TRIAL = SlotState(phase="trial", active_slot="B", trial_slot="B", transaction_id=TX,
                  trial_version="1.1.1", trial_entrypoint="bin/digital-dash", trial_digest="0" * 64)
UNKNOWN = {"schema_version": 1, "state": "unknown", "version": None, "restored_at": None}


class Services:
    def restart_and_wait_healthy(self, unit, timeout_seconds):
        return True


def installed_root(tmp_path):
    root = tmp_path / "cluster"
    entry = root / "slots" / "A" / "bin" / "digital-dash"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n")
    entry.chmod(0o755)
    ABSlotInstaller(root, Services(), service_unit="digital-cluster.service")
    return root


# --- decide(): state partitions -------------------------------------------

def test_schema_version_is_one():
    assert SCHEMA_VERSION == 1


def test_provisioned_stable_uses_initial_version():
    assert decide(SlotSnapshot(SlotState(), "A"), "1.0.0", None, 0.0) == {
        "schema_version": 1, "state": "stable", "version": "1.0.0", "restored_at": None}


def test_recorded_stable_version_wins_over_initial_version():
    stable = SlotState(stable_slot="B", active_slot="B", stable_version="1.1.1")
    assert decide(SlotSnapshot(stable, "B"), "1.0.0", None, 0.0)["version"] == "1.1.1"


def test_selected_trial_slot_reports_trial_version():
    status = decide(SlotSnapshot(TRIAL, "B"), "1.0.0", None, 0.0)
    assert (status["state"], status["version"]) == ("trial", "1.1.1")


def test_activating_after_selector_switch_is_trial():
    # ExecStartPre runs after the selector switch, before the "trial" write.
    activating = replace(TRIAL, phase="activating", active_slot="A")
    status = decide(SlotSnapshot(activating, "B"), "1.0.0", None, 0.0)
    assert (status["state"], status["version"]) == ("trial", "1.1.1")


def test_activating_before_selector_switch_is_still_stable():
    activating = replace(TRIAL, phase="activating", active_slot="A")
    status = decide(SlotSnapshot(activating, "A"), "1.0.0", None, 0.0)
    assert (status["state"], status["version"]) == ("stable", "1.0.0")


def test_rolling_back_with_stable_selected_is_stable_previous_version():
    rolling = replace(TRIAL, phase="rolling_back")
    status = decide(SlotSnapshot(rolling, "A"), "1.0.0", None, 0.0)
    assert (status["state"], status["version"]) == ("stable", "1.0.0")


def test_unreadable_state_is_unknown():
    assert decide(SlotSnapshot(None, "A"), "1.0.0", None, 0.0) == UNKNOWN


def test_missing_selector_is_unknown():
    assert decide(SlotSnapshot(SlotState(), None), "1.0.0", None, 0.0) == UNKNOWN


def test_stable_journal_with_foreign_selected_slot_is_unknown():
    # Journal says no pending update on A, but B is selected: never guess.
    assert decide(SlotSnapshot(SlotState(), "B"), "1.0.0", None, 0.0) == UNKNOWN


@pytest.mark.parametrize("initial", ["", "-1.0", "1.0.0\n", "x" * 65, None])
def test_invalid_initial_version_fallback_is_unknown(initial):
    assert decide(SlotSnapshot(SlotState(), "A"), initial, None, 0.0) == UNKNOWN


# --- read_snapshot(): metadata partitions ---------------------------------

def test_valid_installer_root_is_read(tmp_path):
    snapshot = read_snapshot(installed_root(tmp_path))
    assert snapshot.state.phase == "stable" and snapshot.selected_slot == "A"


def test_missing_install_root_reads_nothing(tmp_path):
    assert read_snapshot(tmp_path / "absent") == SlotSnapshot(None, None)


def test_missing_journal_reads_no_state(tmp_path):
    root = installed_root(tmp_path)
    (root / "state.json").unlink()
    snapshot = read_snapshot(root)
    assert snapshot.state is None and snapshot.selected_slot == "A"


@pytest.mark.parametrize("content", ["{not json", '{"schema_version": 1, "phase": "sta', "", '{"schema_version": 2}'])
def test_corrupt_or_partially_written_journal_reads_no_state(tmp_path, content):
    root = installed_root(tmp_path)
    (root / "state.json").write_text(content)
    assert read_snapshot(root).state is None


@pytest.mark.parametrize("target", ["slots/C", "/etc", "../cluster/slots/A/..", "slots"])
def test_foreign_selector_target_reads_no_slot(tmp_path, target):
    root = installed_root(tmp_path)
    (root / "active-slot").unlink()
    os.symlink(target, root / "active-slot")
    assert read_snapshot(root).selected_slot is None


def test_absolute_selector_is_rejected_like_the_installer(tmp_path):
    # ABSlotInstaller._selected_slot accepts only relative slots/A|B.
    root = installed_root(tmp_path)
    (root / "active-slot").unlink()
    os.symlink(root / "slots" / "A", root / "active-slot")
    assert read_snapshot(root).selected_slot is None


def test_selector_to_missing_slot_directory_reads_no_slot(tmp_path):
    root = installed_root(tmp_path)
    (root / "active-slot").unlink()
    os.symlink("slots/B", root / "active-slot")
    assert read_snapshot(root).selected_slot is None


def test_missing_selector_reads_no_slot(tmp_path):
    root = installed_root(tmp_path)
    (root / "active-slot").unlink()
    assert read_snapshot(root).selected_slot is None


def test_read_snapshot_never_writes(tmp_path):
    root = installed_root(tmp_path)
    before = {p: p.lstat().st_mtime_ns for p in root.rglob("*")}
    read_snapshot(root)
    assert {p: p.lstat().st_mtime_ns for p in root.rglob("*")} == before


# --- output, CLI and watch (Task 2) ----------------------------------------

import json
import stat

from capstone_ota.agent import ui_status
from capstone_ota.agent.ui_status import main, read_previous, run_once, watch, write_status


STABLE_1 = {"schema_version": 1, "state": "stable", "version": "1.0.0", "restored_at": None}


def test_write_status_is_atomic_0644_and_skips_identical(tmp_path):
    out = tmp_path / "digital-cluster.json"
    assert write_status(out, STABLE_1) is True
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    assert json.loads(out.read_text()) == STABLE_1
    before = out.stat().st_mtime_ns
    assert write_status(out, dict(STABLE_1)) is False
    assert out.stat().st_mtime_ns == before
    assert [p.name for p in tmp_path.iterdir()] == ["digital-cluster.json"]


def test_write_status_does_not_create_missing_directory(tmp_path):
    with pytest.raises(OSError):
        write_status(tmp_path / "missing" / "x.json", STABLE_1)
    assert not (tmp_path / "missing").exists()


def test_interrupted_write_keeps_previous_status_and_no_temporary(tmp_path, monkeypatch):
    # Fault injection: the process dies between writing the temp file and rename.
    out = tmp_path / "digital-cluster.json"
    write_status(out, STABLE_1)
    def crash(*args, **kwargs):
        raise OSError("power cut before rename")
    monkeypatch.setattr(ui_status.os, "replace", crash)
    with pytest.raises(OSError):
        write_status(out, {**STABLE_1, "state": "trial", "version": "1.1.1"})
    assert json.loads(out.read_text()) == STABLE_1
    assert [p.name for p in tmp_path.iterdir()] == ["digital-cluster.json"]


@pytest.mark.parametrize("content", ["garbage", "", "[]", json.dumps({**STABLE_1, "schema_version": 2}),
                                     json.dumps({**STABLE_1, "pad": "x" * 5000}),
                                     json.dumps({**STABLE_1, "schema_version": True}), b"\xff\xfe"])
def test_read_previous_rejects_non_v1(tmp_path, content):
    out = tmp_path / "digital-cluster.json"
    out.write_bytes(content if isinstance(content, bytes) else content.encode())
    assert read_previous(out) is None


def test_read_previous_missing_is_none(tmp_path):
    assert read_previous(tmp_path / "absent.json") is None


def test_run_once_writes_current_status(tmp_path):
    root, out = installed_root(tmp_path), tmp_path / "status.json"
    assert run_once(root, "1.0.0", out, now=lambda: 5.0) == STABLE_1
    assert read_previous(out) == STABLE_1


def test_main_snapshot_returns_zero_when_output_directory_missing(tmp_path, capsys):
    root = installed_root(tmp_path)
    code = main(["--install-root", str(root), "--initial-version", "1.0.0",
                 "--output", str(tmp_path / "missing" / "x.json")])
    assert code == 0
    assert "x.json" in capsys.readouterr().err


def test_main_snapshot_writes_status(tmp_path):
    root, out = installed_root(tmp_path), tmp_path / "status.json"
    assert main(["--install-root", str(root), "--initial-version", "1.0.0", "--output", str(out)]) == 0
    assert read_previous(out)["state"] == "stable"


def test_watch_survives_corrupt_state_and_recovers(tmp_path):
    root, out = installed_root(tmp_path), tmp_path / "status.json"
    good = (root / "state.json").read_text()
    seen = []
    def sleep(_):
        seen.append(read_previous(out)["state"])
        (root / "state.json").write_text(good)
    (root / "state.json").write_text("{corrupt")
    watch(root, "1.0.0", out, 1.0, iterations=2, sleep=sleep)
    assert seen == ["unknown", "stable"]


def test_watch_continues_when_output_write_fails(tmp_path):
    root = installed_root(tmp_path)
    calls = []
    watch(root, "1.0.0", tmp_path / "missing" / "x.json", 1.0, iterations=3, sleep=calls.append)
    assert calls == [1.0, 1.0, 1.0]


# --- restored_at state transitions (Task 3) --------------------------------

def _prev(state, version, restored_at=None):
    return {"schema_version": 1, "state": state, "version": version, "restored_at": restored_at}


def test_trial_to_stable_with_different_version_sets_restored_at():
    status = decide(SlotSnapshot(SlotState(), "A"), "1.0.0", _prev("trial", "1.1.1"), 100.0)
    assert (status["state"], status["version"], status["restored_at"]) == ("stable", "1.0.0", 100.0)


def test_restored_at_carries_over_while_stable_same_version():
    status = decide(SlotSnapshot(SlotState(), "A"), "1.0.0", _prev("stable", "1.0.0", 100.0), 200.0)
    assert status["restored_at"] == 100.0


def test_restored_at_cleared_when_stable_version_changes():
    stable = SlotState(stable_slot="B", active_slot="B", stable_version="1.2.0")
    assert decide(SlotSnapshot(stable, "B"), "1.0.0", _prev("stable", "1.0.0", 100.0), 200.0)["restored_at"] is None


def test_commit_same_version_never_sets_restored_at():
    stable = SlotState(stable_slot="B", active_slot="B", stable_version="1.1.1")
    assert decide(SlotSnapshot(stable, "B"), "1.0.0", _prev("trial", "1.1.1"), 100.0)["restored_at"] is None


def test_trial_clears_restored_at():
    assert decide(SlotSnapshot(TRIAL, "B"), "1.0.0", _prev("stable", "1.0.0", 100.0), 200.0)["restored_at"] is None


def test_unknown_previous_never_sets_restored_at():
    assert decide(SlotSnapshot(SlotState(), "A"), "1.0.0", _prev("unknown", None), 100.0)["restored_at"] is None


def test_unknown_now_clears_restored_at():
    assert decide(SlotSnapshot(None, None), "1.0.0", _prev("stable", "1.0.0", 100.0), 200.0)["restored_at"] is None


@pytest.mark.parametrize("bad", ["100", True, -1.0, float("nan"), float("inf")])
def test_malformed_previous_restored_at_is_not_carried(bad):
    status = decide(SlotSnapshot(SlotState(), "A"), "1.0.0", _prev("stable", "1.0.0", bad), 200.0)
    assert status["restored_at"] is None


# --- final review fixes ------------------------------------------------------

def test_read_previous_never_blocks_on_fifo(tmp_path):
    out = tmp_path / "digital-cluster.json"
    os.mkfifo(out)
    assert read_previous(out) is None  # must return immediately, not block


def test_read_previous_does_not_follow_symlink(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(STABLE_1))
    out = tmp_path / "digital-cluster.json"
    out.symlink_to(target)
    assert read_previous(out) is None


def test_write_status_replaces_planted_fifo_or_symlink_without_following(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    for kind in ("fifo", "symlink"):
        out = tmp_path / f"{kind}.json"
        os.mkfifo(out) if kind == "fifo" else out.symlink_to(victim)
        assert write_status(out, STABLE_1) is True
        assert out.is_file() and not out.is_symlink()
    assert victim.read_text() == "keep"


def test_failed_snapshot_removes_stale_status(tmp_path, monkeypatch):
    root, out = installed_root(tmp_path), tmp_path / "status.json"
    write_status(out, {**STABLE_1, "state": "trial", "version": "1.1.1"})
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(ui_status.tempfile, "mkstemp", fail)
    assert main(["--install-root", str(root), "--initial-version", "1.0.0", "--output", str(out)]) == 0
    assert not out.exists()  # the UI then shows unknown instead of a stale TRIAL


def test_failed_watch_iteration_removes_stale_status(tmp_path, monkeypatch):
    root, out = installed_root(tmp_path), tmp_path / "status.json"
    write_status(out, {**STABLE_1, "state": "trial", "version": "1.1.1"})
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(ui_status.tempfile, "mkstemp", fail)
    watch(root, "1.0.0", out, 1.0, iterations=1, sleep=lambda _: None)
    assert not out.exists()
