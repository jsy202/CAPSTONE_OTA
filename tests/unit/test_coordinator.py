import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.common.can_protocol import (
    CanFrame, HeartbeatFrame, OtaStatus, OtaStatusFrame, Slot,
)
from capstone_ota.common.errors import OtaError
from tests.unit.test_compatibility import snapshot
from tests.unit.test_vehicle_bundle import bundle_data, parse


TX = "550e8400-e29b-41d4-a716-446655440000"


def core_api():
    from capstone_ota.coordinator.core import VehicleCoordinator, PreparedArtifact
    return VehicleCoordinator, PreparedArtifact


class Clock:
    value = 10.0

    def __call__(self):
        return self.value


class Services:
    def __init__(self, owner, role):
        self.owner, self.role = owner, role
        self.fail_trial = False

    def restart_and_wait_healthy(self, unit, timeout_seconds):
        self.owner.record(f"restart:{self.role}")
        installer = self.owner.local if self.role == "central-control" else self.owner.remote
        return not (self.fail_trial and installer.active_link.resolve().name == "B")


class Zone:
    """External CAN participant double backed by the real A/B implementation."""
    def __init__(self, owner):
        self.owner = owner
        self.fail = None

    def status(self, transaction_id, token, timeout_s):
        self.owner.record("zone:status")
        state = self.owner.remote.state
        status = OtaStatus.IDLE if state.phase == "stable" else OtaStatus.READY
        return OtaStatusFrame(status, token, Slot[state.active_slot])

    def prepare(self, transaction_id, token, slot, timeout_s):
        self.owner.record("zone:prepare")
        if self.fail == "prepare":
            raise OtaError("ZONE_PREPARE_FAILED", "remote prepare rejected")
        target = self.owner.bundle.targets[1]
        self.owner.remote.stage(transaction_id, self.owner.trees[target.ecu_id], target.entrypoint, target.software_version)
        return OtaStatusFrame(OtaStatus.READY, token, slot)

    def activate(self, transaction_id, token, slot, timeout_s):
        self.owner.record("zone:activate")
        if self.fail == "activate":
            raise OtaError("ZONE_ACTIVATION_FAILED", "lost CAN response")
        self.owner.remote.activate_trial(transaction_id)
        return OtaStatusFrame(OtaStatus.VERIFYING, token, slot)

    def commit(self, transaction_id, token, slot, timeout_s):
        self.owner.record("zone:commit")
        self.owner.remote.commit(transaction_id)
        return OtaStatusFrame(OtaStatus.COMMITTED, token, slot)

    def rollback(self, transaction_id, token, slot, timeout_s):
        self.owner.record("zone:rollback")
        if self.fail == "rollback":
            raise OtaError("ZONE_ROLLBACK_FAILED", "remote unavailable")
        state = self.owner.remote.state
        if state.transaction_id == transaction_id or transaction_id in state.completed_transactions:
            self.owner.remote.rollback(transaction_id)
        return OtaStatusFrame(OtaStatus.ROLLED_BACK, token, Slot[self.owner.remote.state.active_slot])


class Probe:
    def __init__(self, owner):
        self.owner = owner
        self.bad_trial = False
        self.bad_recovery = False
        self.missing_heartbeat = False

    def collect(self, bundle, *, trial, not_before, timeout_s):
        self.owner.record("verify:trial" if trial else "verify:recovery")
        bad = self.bad_trial if trial else self.bad_recovery
        result = snapshot(trial=trial, speed=65 if bad else 650)
        shift = not_before - result.started_at
        frames = []
        for frame in result.frames:
            if frame.can_id in {0x100, 0x101}:
                if trial and self.missing_heartbeat and frame.can_id == 0x101:
                    continue
                heartbeat = HeartbeatFrame.decode(CanFrame(frame.can_id, frame.data))
                installer = self.owner.local if frame.can_id == 0x100 else self.owner.remote
                state = installer.state
                version = state.trial_version if trial else (state.stable_version or "1.2.3")
                heartbeat = replace(heartbeat, software_version=tuple(int(p) for p in version.split(".")))
                frame = replace(frame, data=heartbeat.encode().data)
            frames.append(replace(frame, observed_at=frame.observed_at + shift))
        self.owner.clock.value = not_before + 1.0
        return replace(result, started_at=not_before, ended_at=not_before + 1.0,
            frames=tuple(frames), functional_results=tuple(replace(f, observed_at=f.observed_at + shift) for f in result.functional_results))


class Harness:
    def __init__(self, tmp_path):
        Coordinator, Prepared = core_api()
        self.trace, self.phase_trace, self.clock = [], [], Clock()
        self.state_path = tmp_path / "vehicle.json"
        self.trees, self.artifacts = {}, {}
        data = bundle_data()
        for target in data["targets"]:
            role = target["ecu_id"]
            tree = tmp_path / f"incoming-{role}"
            (tree / "bin").mkdir(parents=True)
            (tree / "bin" / "application").write_text("#!/bin/sh\nexit 0\n")
            (tree / "bin" / "application").chmod(0o755)
            archive = tmp_path / f"{role}.archive"
            archive.write_bytes(role.encode())
            target.update(artifact_size=len(role), artifact_sha256=hashlib.sha256(role.encode()).hexdigest())
            self.trees[role], self.artifacts[role] = tree, archive
        self.stable = parse(data)
        for target in data["targets"]:
            target["software_version"] = "2.0.0"
        self.bundle = parse(data)
        self.local_services = Services(self, "central-control")
        self.remote_services = Services(self, "digital-cluster")
        self.local = ABSlotInstaller(tmp_path / "local", self.local_services, "central-control.service")
        self.remote = ABSlotInstaller(tmp_path / "remote", self.remote_services, "digital-dash.service")
        self.zone, self.probe = Zone(self), Probe(self)
        self.prepare_failure = None

        def prepare(target, artifact):
            self.record(f"cache:{target.ecu_id}")
            if self.prepare_failure == target.ecu_id:
                raise OtaError("ARTIFACT_VERIFICATION_FAILED", "release verification rejected")
            return Prepared(target.ecu_id, target.software_version, target.entrypoint, artifact, self.trees[target.ecu_id])

        self.options = dict(state_path=self.state_path, installer=self.local, zone=self.zone,
            probe=self.probe, prepare_artifact=prepare, stable_bundle=self.stable,
            publish_artifacts=lambda bundle, prepared: self.record("publish"),
            maintenance=lambda enabled: self.record(f"maintenance:{enabled}"),
            progress=lambda event: self.record(f"event:{event['phase']}"),
            monotonic=self.clock,
            now=lambda: datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc))
        self.coordinator = Coordinator(**self.options)

    def record(self, action):
        self.trace.append(action)
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            self.phase_trace.append((action, state["phase"]))

    def execute(self):
        return self.coordinator.execute(self.bundle, self.artifacts)


def test_both_targets_cached_staged_ready_and_verified_before_commit(tmp_path):
    h = Harness(tmp_path)
    result = h.execute()
    assert result.phase == "COMMITTED"
    assert h.local.state.stable_version == h.remote.state.stable_version == "2.0.0"
    assert h.trace.index("cache:digital-cluster") < h.trace.index("zone:prepare")
    assert h.trace.index("zone:prepare") < h.trace.index("zone:activate")
    assert h.trace.index("zone:prepare") < h.trace.index("restart:central-control")
    assert h.trace.index("verify:trial") < h.trace.index("zone:commit")
    assert ("zone:activate", "ACTIVATING") in h.phase_trace
    assert ("zone:commit", "VERIFYING") in h.phase_trace
    assert h.coordinator.state.ecu_states["central-control"]["commit"] == "done"
    assert h.coordinator.state.ecu_states["digital-cluster"]["commit"] == "done"
    events = h.coordinator.state.events
    assert [event["phase"] for event in events] == ["PREPARING", "READY", "ACTIVATING", "VERIFYING", "COMMITTED"]
    assert json.loads(json.dumps(result.to_dict()))["transaction_id"] == TX


@pytest.mark.parametrize("failure", ["local-cache", "remote-cache", "remote-prepare", "hash", "dependency"])
def test_prepare_failure_aborts_without_changing_active_slots(tmp_path, failure):
    h = Harness(tmp_path)
    if failure == "local-cache":
        h.prepare_failure = "central-control"
    elif failure == "remote-cache":
        h.prepare_failure = "digital-cluster"
    elif failure == "remote-prepare":
        h.zone.fail = "prepare"
    elif failure == "hash":
        h.artifacts["digital-cluster"].write_bytes(b"tampered")
    else:
        from tests.unit.test_vehicle_bundle import dependency_data
        data = json.loads(h.bundle.canonical_bytes())
        data["dependencies"] = [dict(dependency_data(), protocol_major=2)]
        h.bundle = parse(data)
    result = h.execute()
    assert result.phase == "ABORTED"
    assert h.local.active_link.resolve().name == h.remote.active_link.resolve().name == "A"
    assert h.local.state.phase == h.remote.state.phase == "stable"
    assert "zone:activate" not in h.trace
    assert "restart:central-control" not in h.trace
    if failure in {"local-cache", "remote-cache", "hash", "dependency"}:
        assert "zone:prepare" not in h.trace


def test_completed_execute_is_idempotent_and_rejects_changed_bundle(tmp_path):
    h = Harness(tmp_path)
    assert h.execute().phase == "COMMITTED"
    before = h.trace.copy()
    assert h.execute().phase == "COMMITTED"
    assert h.trace == before
    data = json.loads(h.bundle.canonical_bytes())
    data["bundle_version"] = "9.0.0"
    with pytest.raises(OtaError, match="TRANSACTION_CONFLICT"):
        h.coordinator.execute(parse(data), h.artifacts)


def test_unknown_state_blocks_activation(tmp_path):
    h = Harness(tmp_path)
    data = json.loads(h.state_path.read_text())
    data["phase"] = "FUTURE"
    h.state_path.write_text(json.dumps(data))
    with pytest.raises(OtaError, match="TRANSACTION_STATE_INVALID"):
        h.execute()
    assert "zone:activate" not in h.trace


@pytest.mark.parametrize("failure", ["remote-activation", "local-activation", "semantic", "heartbeat"])
def test_activation_or_verification_failure_restores_both_and_revalidates(tmp_path, failure):
    h = Harness(tmp_path)
    if failure == "remote-activation":
        h.zone.fail = "activate"
    elif failure == "local-activation":
        h.local_services.fail_trial = True
    elif failure == "semantic":
        h.probe.bad_trial = True
    else:
        h.probe.missing_heartbeat = True
    result = h.execute()
    assert result.phase == "ROLLED_BACK"
    assert h.local.state.stable_slot == h.remote.state.stable_slot == "A"
    assert "verify:recovery" in h.trace
    assert ("zone:rollback", "ROLLING_BACK") in h.phase_trace
    assert ("verify:recovery", "RECOVERY_VERIFYING") in h.phase_trace
    if failure != "remote-activation":
        remote_rollback = h.trace.index("zone:rollback")
        local_restart = next(i for i, action in enumerate(h.trace) if i > remote_rollback and action == "restart:central-control")
        assert remote_rollback < local_restart
    assert h.coordinator.state.stable_bundle["targets"][0]["software_version"] == "1.2.3"


def test_failed_recovery_never_reports_successful_rollback(tmp_path):
    h = Harness(tmp_path)
    h.probe.bad_trial = h.probe.bad_recovery = True
    assert h.execute().phase == "RECOVERY_FAILED"
    assert h.local.state.stable_slot == h.remote.state.stable_slot == "A"
    assert h.coordinator.state.last_error["code"] == "FUNCTIONAL_VALUE_MISMATCH"
    data = json.loads(h.bundle.canonical_bytes())
    data["transaction_id"] = "660e8400-e29b-41d4-a716-446655440000"
    with pytest.raises(OtaError, match="RECOVERY_REQUIRED"):
        h.coordinator.execute(parse(data), h.artifacts)


def test_remote_rollback_failure_is_recovery_failed_even_with_healthy_probe(tmp_path):
    h = Harness(tmp_path)
    h.probe.bad_trial = True
    h.zone.fail = "rollback"
    assert h.execute().phase == "RECOVERY_FAILED"
    assert h.local.state.stable_slot == "A"
    assert h.remote.state.phase == "trial"
    assert any(item["check"] == "rollback" and not item["passed"] for item in h.coordinator.state.evidence)


class PowerCut(BaseException):
    pass


@pytest.mark.parametrize("cut", ["remote-after", "local-after", "verifying", "rollback-remote",
                                 "rollback-local", "recovery-verifying", "partial-commit"])
def test_restart_rolls_back_whole_bundle_and_resumes_idempotently(tmp_path, monkeypatch, cut):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    target, name = {
        "remote-after": (h.zone, "activate"), "local-after": (h.local, "activate_trial"),
        "verifying": (h.probe, "collect"), "rollback-remote": (h.zone, "rollback"),
        "rollback-local": (h.local, "rollback"), "recovery-verifying": (h.probe, "collect"),
        "partial-commit": (h.zone, "commit"),
    }[cut]
    original = getattr(target, name)
    if cut.startswith("rollback") or cut == "recovery-verifying":
        h.probe.bad_trial = True

    def interrupted(*args, **kwargs):
        if cut == "verifying":
            raise PowerCut()
        if cut == "recovery-verifying" and kwargs["trial"]:
            return original(*args, **kwargs)
        original(*args, **kwargs)
        raise PowerCut()

    monkeypatch.setattr(target, name, interrupted)
    with pytest.raises(PowerCut):
        h.execute()
    monkeypatch.setattr(target, name, original)
    h.coordinator = Coordinator(**h.options)
    result = h.coordinator.recover_on_startup()
    expected = "RECOVERY_FAILED" if cut == "partial-commit" else "ROLLED_BACK"
    assert result.phase == expected
    assert h.local.state.stable_slot == "A"
    if cut != "partial-commit":
        assert h.remote.state.stable_slot == "A"
    else:
        assert h.remote.state.stable_slot == "B"
    before = h.trace.copy()
    assert h.coordinator.recover_on_startup().phase == expected
    assert h.trace == before


@pytest.mark.parametrize("phase", ["PREPARING", "READY"])
def test_restart_before_activation_aborts_and_cleans_staging(tmp_path, monkeypatch, phase):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    original = h.coordinator._transition

    def cut_after(destination):
        original(destination)
        if destination == phase:
            raise PowerCut()

    monkeypatch.setattr(h.coordinator, "_transition", cut_after)
    with pytest.raises(PowerCut):
        h.execute()
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "ABORTED"
    assert h.local.state.phase == h.remote.state.phase == "stable"
    assert h.local.state.stable_slot == h.remote.state.stable_slot == "A"
    assert "zone:activate" not in h.trace


def test_committed_restart_preserves_selected_slots(tmp_path):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    h.execute()
    before = h.trace.copy()
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "COMMITTED"
    assert h.local.state.stable_slot == h.remote.state.stable_slot == "B"
    assert h.trace == before


def test_cluster_rollback_reply_must_confirm_restored_stable_slot(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    h.probe.bad_trial = True
    original = h.zone.rollback

    def wrong_slot(*args):
        return replace(original(*args), slot=Slot.B)

    monkeypatch.setattr(h.zone, "rollback", wrong_slot)
    assert h.execute().phase == "RECOVERY_FAILED"
    assert h.coordinator.state.ecu_states["digital-cluster"]["error"]["code"] == "ZONE_STATUS_INVALID"


@pytest.mark.parametrize("field,value", [("transaction_token", 123), ("slot", Slot.A),
                                        ("status", OtaStatus.COMMITTED), ("detail", 5)])
def test_bad_ready_ack_cannot_authorize_activation(tmp_path, monkeypatch, field, value):
    h = Harness(tmp_path)
    original = h.zone.prepare
    monkeypatch.setattr(h.zone, "prepare", lambda *args: replace(original(*args), **{field: value}))
    assert h.execute().phase == "ABORTED"
    assert "zone:activate" not in h.trace
    assert h.coordinator.state.last_error["code"] == "ZONE_STATUS_INVALID"


def test_discovery_rejects_wrong_token_before_local_staging(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    original = h.zone.status
    monkeypatch.setattr(h.zone, "status", lambda *args: replace(original(*args), transaction_token=123))
    assert h.execute().phase == "ABORTED"
    assert h.local.state.completed_transactions == {}


def test_phase_timeout_is_shared_by_both_cache_operations(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    original = h.coordinator.prepare_artifact

    def slow(target, artifact):
        result = original(target, artifact)
        h.clock.value += 40
        return result

    monkeypatch.setattr(h.coordinator, "prepare_artifact", slow)
    assert h.execute().phase == "ABORTED"
    assert h.coordinator.state.last_error["code"] == "ACTION_TIMEOUT"
    assert "zone:prepare" not in h.trace


def test_committed_promotion_and_prior_baseline_are_atomic(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    original = h.coordinator._transition

    def interrupt(destination):
        if destination == "COMMITTED":
            raise PowerCut()
        original(destination)

    monkeypatch.setattr(h.coordinator, "_transition", interrupt)
    with pytest.raises(PowerCut):
        h.execute()
    assert h.coordinator.state.phase == "VERIFYING"
    assert h.coordinator.state.stable_bundle["targets"][0]["software_version"] == "1.2.3"
    assert {ecu["stable_slot"] for ecu in h.coordinator.state.ecu_states.values()} == {"A"}
    assert {ecu["trial_slot"] for ecu in h.coordinator.state.ecu_states.values()} == {"B"}


def test_aggregate_verifying_event_reports_actual_trial_slots(tmp_path):
    h = Harness(tmp_path)
    h.execute()
    event = next(e for e in h.coordinator.state.events if e["phase"] == "VERIFYING")
    assert {ecu["active_slot"] for ecu in event["ecu_states"].values()} == {"B"}


def test_probe_future_timestamp_cannot_commit(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    original = h.probe.collect

    def future(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, ended_at=result.ended_at + 1)

    monkeypatch.setattr(h.probe, "collect", future)
    assert h.execute().phase == "RECOVERY_FAILED"
    assert h.coordinator.state.last_error["code"] == "OBSERVATION_WINDOW_INVALID"
    assert "zone:commit" not in h.trace


def test_failed_abort_cleanup_retries_remote_on_startup(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    original = h.zone.prepare
    monkeypatch.setattr(h.zone, "prepare", lambda *args: replace(original(*args), detail=5))
    h.zone.fail = "rollback"
    assert h.execute().phase == "ABORTED"
    assert h.local.state.phase == "stable"
    assert h.remote.state.phase == "staged"
    h.zone.fail = None
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "ABORTED"
    assert h.remote.state.phase == "stable"


def test_old_completed_transaction_returns_recorded_failure_evidence(tmp_path):
    h = Harness(tmp_path)
    h.probe.bad_trial = True
    first_bundle = h.bundle
    first = h.execute().to_dict()
    h.probe.bad_trial = False
    data = json.loads(h.bundle.canonical_bytes())
    data["transaction_id"] = "660e8400-e29b-41d4-a716-446655440000"
    h.bundle = parse(data)
    assert h.execute().phase == "COMMITTED"
    assert h.coordinator.execute(first_bundle, h.artifacts).to_dict() == first


def test_restart_after_terminal_write_releases_maintenance_idempotently(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    original = h.coordinator.maintenance

    def cut_release(enabled):
        if not enabled:
            raise PowerCut()
        original(enabled)

    monkeypatch.setattr(h.coordinator, "maintenance", cut_release)
    with pytest.raises(PowerCut):
        h.execute()
    assert h.coordinator.state.phase == "COMMITTED"
    assert "maintenance:False" not in h.trace
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "COMMITTED"
    assert "maintenance:False" in h.trace
    before = h.trace.copy()
    h.coordinator.recover_on_startup()
    assert h.trace == before


def test_journal_write_failure_prevents_any_external_action(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    from capstone_ota.coordinator.state import VehicleTransactionState

    def disk_full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(VehicleTransactionState, "save_atomic", disk_full)
    with pytest.raises(OSError, match="disk full"):
        h.execute()
    assert h.trace == []
    assert h.local.state.phase == h.remote.state.phase == "stable"


def test_pre_activation_recovery_cleans_interrupted_local_staging_tree(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    original = h.coordinator._transition

    def cut(destination):
        original(destination)
        if destination == "READY":
            raise PowerCut()

    monkeypatch.setattr(h.coordinator, "_transition", cut)
    with pytest.raises(PowerCut):
        h.execute()
    interrupted_tree = h.local.slots_dir / f".staging-{TX}"
    interrupted_tree.mkdir()
    (interrupted_tree / "partial").write_text("partial copied application")
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "ABORTED"
    assert not interrupted_tree.exists()
    assert "restart:central-control" not in h.trace


def test_interrupted_abort_cleanup_resumes_after_slot_rollback_completed(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    Coordinator, _ = core_api()
    h.zone.fail = "prepare"
    original = h.local.recover_on_startup
    monkeypatch.setattr(h.local, "recover_on_startup", lambda: (_ for _ in ()).throw(PowerCut()))
    with pytest.raises(PowerCut):
        h.execute()
    interrupted_tree = h.local.slots_dir / f".staging-{TX}"
    interrupted_tree.mkdir()
    (interrupted_tree / "partial").write_text("partial application")
    monkeypatch.setattr(h.local, "recover_on_startup", original)
    h.coordinator = Coordinator(**h.options)
    assert h.coordinator.recover_on_startup().phase == "ABORTED"
    assert not interrupted_tree.exists()
