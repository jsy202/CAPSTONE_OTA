import importlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from capstone_ota.common.errors import OtaError


TX = "00000000-0000-4000-8000-000000000001"
OTHER_TX = "00000000-0000-4000-8000-000000000002"


class Services:
    def __init__(self, results=(True,)):
        self.results = iter(results)
        self.calls = []

    def restart_and_wait_healthy(self, unit, timeout_seconds):
        self.calls.append((unit, timeout_seconds))
        return next(self.results)


def application(root, name="incoming", executable=True):
    tree = root / name
    (tree / "bin").mkdir(parents=True)
    entry = tree / "bin" / "app"
    entry.write_text(f"#!/bin/sh\necho {name}\n")
    entry.chmod(0o755 if executable else 0o644)
    return tree


def installer_at(tmp_path, results=(True,)):
    slots = importlib.import_module("capstone_ota.agent.slots")
    root = tmp_path / "install"
    application(root / "slots", "A")
    services = Services(results)
    return slots.ABSlotInstaller(root, services, service_unit="central-control.service"), services


def test_initialization_durably_selects_a_as_stable(tmp_path):
    installer, services = installer_at(tmp_path)
    state = installer.state
    assert (state.stable_slot, state.active_slot, state.trial_slot, state.phase) == (
        "A", "A", None, "stable"
    )
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert services.calls == []
    assert json.loads(installer.state_path.read_text())["schema_version"] == 1


def test_stage_replaces_only_inactive_slot_and_preserves_input(tmp_path):
    installer, services = installer_at(tmp_path)
    old_stable = (installer.slots_dir / "A" / "bin" / "app").read_text()
    application(installer.slots_dir, "B")
    (installer.slots_dir / "B" / "obsolete").write_text("old")
    incoming = application(tmp_path)

    state = installer.stage(TX, incoming, "bin/app", "2.0")

    assert (state.phase, state.stable_slot, state.active_slot, state.trial_slot) == (
        "staged", "A", "A", "B"
    )
    assert state.transaction_id == TX
    assert state.trial_version == "2.0"
    assert (installer.slots_dir / "B" / "bin" / "app").read_text() == (incoming / "bin" / "app").read_text()
    assert not (installer.slots_dir / "B" / "obsolete").exists()
    assert (installer.slots_dir / "A" / "bin" / "app").read_text() == old_stable
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert incoming.is_dir()
    assert services.calls == []
    assert installer.stage(UUID(TX), incoming, "bin/app", "2.0") == state


@pytest.mark.parametrize("entrypoint,executable", [
    ("bin/missing", True), ("bin/app", False), ("../bin/app", True),
    ("/bin/app", True), ("bin//app", True), ("bin/./app", True),
])
def test_stage_rejects_invalid_entrypoint_without_replacing_content(tmp_path, entrypoint, executable):
    installer, _ = installer_at(tmp_path)
    application(installer.slots_dir, "B")
    incoming = application(tmp_path, executable=executable)
    before = (installer.slots_dir / "B" / "bin" / "app").read_text()
    with pytest.raises(OtaError):
        installer.stage(TX, incoming, entrypoint, "2.0")
    assert (installer.slots_dir / "B" / "bin" / "app").read_text() == before
    assert installer.state.phase == "stable"


def test_stage_rejects_symlinked_tree(tmp_path):
    installer, _ = installer_at(tmp_path)
    incoming = application(tmp_path)
    (incoming / "extra").symlink_to("bin/app")
    with pytest.raises(OtaError):
        installer.stage(TX, incoming, "bin/app", "2.0")
    assert installer.state.phase == "stable"


def test_stage_rejects_second_transaction_and_changed_duplicate(tmp_path):
    installer, _ = installer_at(tmp_path)
    incoming = application(tmp_path)
    installer.stage(TX, incoming, "bin/app", "2.0")
    with pytest.raises(OtaError):
        installer.stage(OTHER_TX, incoming, "bin/app", "2.0")
    (incoming / "bin" / "app").write_text("changed")
    with pytest.raises(OtaError):
        installer.stage(TX, incoming, "bin/app", "2.0")


def test_copy_failure_preserves_old_inactive_content(tmp_path, monkeypatch):
    installer, _ = installer_at(tmp_path)
    application(installer.slots_dir, "B")
    incoming = application(tmp_path)
    def fail_copy(*args, **kwargs):
        raise OSError("copy interrupted")
    monkeypatch.setattr("capstone_ota.agent.slots.shutil.copytree", fail_copy)
    with pytest.raises(OSError):
        installer.stage(TX, incoming, "bin/app", "2.0")
    assert (installer.slots_dir / "B" / "bin" / "app").read_text() == "#!/bin/sh\necho B\n"
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_state_atomic_save_syncs_file_before_replace_and_parent_after(tmp_path, monkeypatch):
    installer, _ = installer_at(tmp_path)
    events = []
    real_sync, real_replace = os.fsync, os.replace
    def sync(fd):
        events.append("directory" if os.path.isdir(f"/proc/self/fd/{fd}") else "file")
        real_sync(fd)
    def replace(source, target):
        events.append("replace")
        return real_replace(source, target)
    monkeypatch.setattr("capstone_ota.agent.slots.os.fsync", sync)
    monkeypatch.setattr("capstone_ota.agent.slots.os.replace", replace)
    installer.state.save_atomic(installer.state_path)
    assert events == ["file", "replace", "directory"]


def test_state_load_rejects_unknown_or_corrupt_schema(tmp_path):
    installer, _ = installer_at(tmp_path)
    slots = importlib.import_module("capstone_ota.agent.slots")
    data = json.loads(installer.state_path.read_text())
    data["unexpected"] = True
    installer.state_path.write_text(json.dumps(data))
    with pytest.raises(OtaError):
        slots.SlotState.load(installer.state_path)
    installer.state_path.write_text("{broken")
    with pytest.raises(OtaError):
        slots.SlotState.load(installer.state_path)


def test_trial_switches_selector_and_restarts_without_promoting_stable(tmp_path):
    installer, services = installer_at(tmp_path)
    incoming = application(tmp_path)
    installer.stage(TX, incoming, "bin/app", "2.0")
    trial = installer.activate_trial(TX)
    assert (trial.phase, trial.active_slot, trial.stable_slot, trial.trial_slot) == (
        "trial", "B", "A", "B"
    )
    assert installer.active_link.resolve() == installer.slots_dir / "B"
    assert services.calls == [("central-control.service", 15)]
    assert installer.activate_trial(TX) == trial
    assert services.calls == [("central-control.service", 15)]
    with pytest.raises(OtaError):
        installer.stage(OTHER_TX, incoming, "bin/app", "3.0")


def test_commit_promotes_trial_and_delayed_duplicates_do_not_reinstall(tmp_path):
    installer, services = installer_at(tmp_path, (True, True))
    incoming = application(tmp_path)
    installer.stage(TX, incoming, "bin/app", "2.0")
    installer.activate_trial(TX)
    committed = installer.commit(TX)
    assert (committed.phase, committed.stable_slot, committed.active_slot, committed.trial_slot) == (
        "stable", "B", "B", None
    )
    assert committed.stable_version == "2.0"
    assert committed.completed_transactions[TX] == "committed"
    assert installer.commit(TX) == committed
    assert installer.activate_trial(TX) == committed
    assert len(services.calls) == 1
    installer.stage(OTHER_TX, incoming, "bin/app", "3.0")
    installer.activate_trial(OTHER_TX)
    current = installer.commit(OTHER_TX)
    assert current.stable_slot == "A"
    before = (installer.slots_dir / "B" / "bin" / "app").read_text()
    assert installer.stage(TX, tmp_path / "no-longer-present", "bin/app", "2.0") == current
    assert installer.commit(TX) == current
    assert (installer.slots_dir / "B" / "bin" / "app").read_text() == before
    assert len(services.calls) == 2


def test_rollback_restores_stable_and_is_idempotent(tmp_path):
    installer, services = installer_at(tmp_path, (True, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    restored = installer.rollback(TX)
    assert (restored.phase, restored.active_slot, restored.stable_slot, restored.trial_slot) == (
        "stable", "A", "A", None
    )
    assert restored.completed_transactions[TX] == "rolled_back"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert services.calls == [("central-control.service", 15)] * 2
    assert installer.rollback(TX) == restored
    assert installer.activate_trial(TX) == restored
    with pytest.raises(OtaError):
        installer.commit(TX)
    assert len(services.calls) == 2


def test_rollback_staged_application_does_not_restart_unchanged_application(tmp_path):
    installer, services = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    assert installer.rollback(TX).stable_slot == "A"
    assert services.calls == []


@pytest.mark.parametrize("command", ["activate_trial", "commit", "rollback"])
def test_commands_reject_wrong_transaction_without_switching(tmp_path, command):
    installer, services = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    with pytest.raises(OtaError):
        getattr(installer, command)(OTHER_TX)
    assert installer.state.phase == "staged"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert services.calls == []


def test_commit_before_trial_is_rejected(tmp_path):
    installer, _ = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    with pytest.raises(OtaError):
        installer.commit(TX)
    assert installer.state.stable_slot == "A"


def test_unhealthy_trial_restores_stable_before_reporting_failure(tmp_path):
    installer, services = installer_at(tmp_path, (False, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    with pytest.raises(OtaError) as error:
        installer.activate_trial(TX)
    assert error.value.code == "HEALTH_CHECK_FAILED"
    assert installer.state.phase == "stable"
    assert installer.state.completed_transactions[TX] == "rolled_back"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert services.calls == [("central-control.service", 15)] * 2


def test_unhealthy_rollback_keeps_pending_intent_for_retry(tmp_path):
    installer, services = installer_at(tmp_path, (True, False, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    with pytest.raises(OtaError) as error:
        installer.rollback(TX)
    assert error.value.code == "ROLLBACK_FAILED"
    assert installer.state.phase == "rolling_back"
    assert installer.state.stable_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert installer.rollback(TX).phase == "stable"
    assert len(services.calls) == 3


def test_activation_persists_intent_before_exposing_trial(tmp_path, monkeypatch):
    installer, services = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    real_select = installer._select
    def select(slot):
        assert installer.state.phase == "activating"
        assert installer.state.stable_slot == "A"
        real_select(slot)
    monkeypatch.setattr(installer, "_select", select)
    installer.activate_trial(TX)


@pytest.mark.parametrize("phase", ["staging", "staged", "trial", "committing", "rolling_back"])
def test_startup_recovery_aborts_every_uncommitted_phase(tmp_path, phase):
    installer, services = installer_at(tmp_path, (True, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    if phase in {"trial", "committing", "rolling_back"}:
        installer.activate_trial(TX)
    replace(installer.state, phase=phase).save_atomic(installer.state_path)
    slots = importlib.import_module("capstone_ota.agent.slots")
    restarted = slots.ABSlotInstaller(installer.install_root, services, "central-control.service")
    restored = restarted.recover_on_startup()
    assert (restored.phase, restored.stable_slot, restored.active_slot, restored.trial_slot) == (
        "stable", "A", "A", None
    )
    assert restored.completed_transactions[TX] == "rolled_back"
    assert restarted.active_link.resolve() == restarted.slots_dir / "A"
    expected_calls = 2 if phase in {"trial", "committing", "rolling_back"} else 0
    assert len(services.calls) == expected_calls
    assert restarted.recover_on_startup() == restored
    assert len(services.calls) == expected_calls


def test_recovery_cleans_interrupted_staging_and_backup(tmp_path, monkeypatch):
    installer, _ = installer_at(tmp_path)
    incoming = application(tmp_path)
    application(installer.slots_dir, "B")
    real_replace = os.replace
    def fail_promote(source, target):
        if Path(target) == installer.slots_dir / "B":
            raise OSError("power loss after backup")
        return real_replace(source, target)
    monkeypatch.setattr("capstone_ota.agent.slots.os.replace", fail_promote)
    with pytest.raises(OSError):
        installer.stage(TX, incoming, "bin/app", "2.0")
    assert installer.state.phase == "staging"
    assert (installer.slots_dir / f".backup-{TX}").is_dir()
    # Model a partially copied tree surviving process death.
    application(installer.slots_dir, f".staging-{TX}")
    monkeypatch.setattr("capstone_ota.agent.slots.os.replace", real_replace)
    installer.recover_on_startup()
    assert not (installer.slots_dir / f".backup-{TX}").exists()
    assert not (installer.slots_dir / f".staging-{TX}").exists()
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_recovery_restores_stable_if_trial_save_failed_after_activation(tmp_path, monkeypatch):
    installer, services = installer_at(tmp_path, (True, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    slots = importlib.import_module("capstone_ota.agent.slots")
    original_save = slots.SlotState.save_atomic
    def interrupted_save(state, path):
        if state.phase == "trial":
            raise OSError("state write interrupted after restart")
        return original_save(state, path)
    monkeypatch.setattr(slots.SlotState, "save_atomic", interrupted_save)
    with pytest.raises(OSError):
        installer.activate_trial(TX)
    assert installer.state.phase == "activating"
    assert installer.state.active_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "B"
    monkeypatch.setattr(slots.SlotState, "save_atomic", original_save)
    restarted = slots.ABSlotInstaller(installer.install_root, services, "central-control.service")
    assert restarted.recover_on_startup().stable_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert len(services.calls) == 2


def test_recovery_inspects_selector_when_journal_still_says_staged(tmp_path):
    installer, services = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.active_link.unlink()
    installer.active_link.symlink_to("slots/B")
    assert installer.recover_on_startup().active_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert services.calls == [("central-control.service", 15)]


def test_interrupted_commit_recovers_previous_stable(tmp_path, monkeypatch):
    installer, services = installer_at(tmp_path, (True, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    slots = importlib.import_module("capstone_ota.agent.slots")
    original_save = slots.SlotState.save_atomic
    def fail_promotion(state, path):
        if state.phase == "stable" and state.stable_slot == "B":
            raise OSError("stable promotion interrupted")
        return original_save(state, path)
    monkeypatch.setattr(slots.SlotState, "save_atomic", fail_promotion)
    with pytest.raises(OSError):
        installer.commit(TX)
    assert installer.state.phase == "committing"
    assert installer.recover_on_startup().stable_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_committed_startup_preserves_promoted_slot(tmp_path):
    installer, services = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    installer.commit(TX)
    assert installer.recover_on_startup().stable_slot == "B"
    assert installer.active_link.resolve() == installer.slots_dir / "B"
    assert len(services.calls) == 1


def test_corrupt_startup_blocks_without_changing_selector(tmp_path):
    installer, _ = installer_at(tmp_path)
    installer.state_path.write_text('{"schema_version": 9}')
    slots = importlib.import_module("capstone_ota.agent.slots")
    with pytest.raises(OtaError):
        slots.ABSlotInstaller(installer.install_root, Services())
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_recovery_retries_unhealthy_stable_restart(tmp_path):
    installer, services = installer_at(tmp_path, (True, False, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    with pytest.raises(OtaError):
        installer.recover_on_startup()
    assert installer.state.phase == "rolling_back"
    assert installer.recover_on_startup().phase == "stable"
    assert len(services.calls) == 3


def test_stage_refuses_to_replace_slot_selected_by_disagreeing_journal(tmp_path):
    installer, _ = installer_at(tmp_path)
    application(installer.slots_dir, "B")
    installer.active_link.unlink()
    installer.active_link.symlink_to("slots/B")
    with pytest.raises(OtaError):
        installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    assert (installer.slots_dir / "B" / "bin" / "app").read_text() == "#!/bin/sh\necho B\n"


@pytest.mark.parametrize("source_kind", ["stable_slot", "ancestor"])
def test_stage_rejects_source_overlapping_owned_installation(tmp_path, source_kind):
    installer, _ = installer_at(tmp_path)
    incoming = installer.slots_dir / "A" if source_kind == "stable_slot" else tmp_path
    if source_kind == "ancestor":
        (incoming / "bin").mkdir()
        (incoming / "bin" / "app").write_text("#!/bin/sh\n")
        (incoming / "bin" / "app").chmod(0o755)
    with pytest.raises(OtaError):
        installer.stage(TX, incoming, "bin/app", "2.0")
    assert installer.state.phase == "stable"


def test_constructor_rejects_symlinked_slot_root(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "slots").symlink_to(outside, target_is_directory=True)
    slots = importlib.import_module("capstone_ota.agent.slots")
    with pytest.raises(OtaError):
        slots.ABSlotInstaller(root, Services())
    assert list(outside.iterdir()) == []


def test_rollback_refuses_missing_stable_slot_without_selecting_broken_link(tmp_path):
    installer, _ = installer_at(tmp_path, (True, True))
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    (installer.slots_dir / "A" / "bin" / "app").unlink()
    (installer.slots_dir / "A" / "bin").rmdir()
    (installer.slots_dir / "A").rmdir()
    with pytest.raises(OtaError):
        installer.rollback(TX)
    assert installer.active_link.resolve() == installer.slots_dir / "B"
    assert installer.state.stable_slot == "A"


def test_duplicate_trial_command_rejects_changed_selector(tmp_path):
    installer, _ = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.activate_trial(TX)
    installer.active_link.unlink()
    installer.active_link.symlink_to("slots/A")
    with pytest.raises(OtaError):
        installer.activate_trial(TX)


def test_state_rejects_duplicate_json_fields(tmp_path):
    installer, _ = installer_at(tmp_path)
    text = installer.state_path.read_text().rstrip()
    installer.state_path.write_text(text[:-1] + ',"stable_slot":"A"}')
    slots = importlib.import_module("capstone_ota.agent.slots")
    with pytest.raises(OtaError):
        slots.SlotState.load(installer.state_path)


def test_stage_accepts_extracted_application_under_installation_staging(tmp_path):
    installer, _ = installer_at(tmp_path)
    incoming = application(installer.install_root / "staging")
    assert installer.stage(TX, incoming, "bin/app", "2.0").trial_slot == "B"
    assert incoming.is_dir()
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_constructor_rejects_journal_inside_replaceable_slot(tmp_path):
    root = tmp_path / "install"
    slots = importlib.import_module("capstone_ota.agent.slots")
    with pytest.raises(OtaError):
        slots.ABSlotInstaller(root, Services(), state_path=root / "slots" / "B" / "state.json")


def test_recovery_rejects_replaced_slot_root_before_cleanup(tmp_path):
    installer, _ = installer_at(tmp_path)
    installer.stage(TX, application(tmp_path), "bin/app", "2.0")
    installer.rollback(TX)
    outside = tmp_path / "outside"
    outside.mkdir()
    application(outside, f".backup-{TX}")
    installer.slots_dir.rename(installer.install_root / "old-slots")
    installer.slots_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OtaError):
        installer.recover_on_startup()
    assert (outside / f".backup-{TX}" / "bin" / "app").exists()


@pytest.mark.parametrize("command", ["stage", "recover_on_startup"])
def test_missing_stable_slot_blocks_new_work_and_recovery(tmp_path, command):
    installer, _ = installer_at(tmp_path)
    (installer.slots_dir / "A").rename(installer.install_root / "lost-a")
    with pytest.raises(OtaError):
        if command == "stage":
            installer.stage(TX, application(tmp_path), "bin/app", "2.0")
        else:
            installer.recover_on_startup()
    assert installer.state.phase == "stable"
