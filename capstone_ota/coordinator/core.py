"""Injected, serialized whole-vehicle application update coordinator v1.

Only central-control.service is managed here. Coordinator and Zonal agents are
stable services outside application slots. Call recover_on_startup before serving
commands; execute also refuses pending work until recovery completes.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.common.can_protocol import OtaStatus, OtaStatusFrame, Slot
from capstone_ota.common.errors import OtaError
from capstone_ota.common.vehicle_bundle import EcuTarget, VehicleBundleManifest

from .compatibility import CompatibilitySnapshot, CompatibilityValidator
from .state import TERMINAL_STATES, VehicleTransactionState


class ZoneClient(Protocol):
    """Synchronous CRC/fresh-counter CAN adapter contract v1.

    Mutations bind canonical UUID, retained uint32 token and owning trial slot.
    Adapter handles retries/QUERY resynchronization and enforces timeout_s while
    waiting (core additionally rejects late results). Returned OtaStatusFrame
    must match token, expected phase and detail=0. READY/VERIFYING/COMMITTED
    replies carry the trial slot; ROLLED_BACK carries the restored stable slot.
    status(None, 0, timeout)
    discovers actual selected stable slot before any PREPARE. Artifacts must be
    published at /transactions/<token:08x>/cluster/ before prepare is called.
    rollback of a PREPARE that may not have reached Cluster must safely discover
    and establish stable/no-pending state, then return ROLLED_BACK; never invent
    that acknowledgement after a CAN timeout.
    """
    def prepare(self, transaction_id: str, token: int, slot: Slot, timeout_s: int) -> OtaStatusFrame: ...
    def activate(self, transaction_id: str, token: int, slot: Slot, timeout_s: int) -> OtaStatusFrame: ...
    def commit(self, transaction_id: str, token: int, slot: Slot, timeout_s: int) -> OtaStatusFrame: ...
    def rollback(self, transaction_id: str, token: int, slot: Slot, timeout_s: int) -> OtaStatusFrame: ...
    def status(self, transaction_id: str | None, token: int, timeout_s: int) -> OtaStatusFrame: ...


class CompatibilityProbe(Protocol):
    """Collect actual process/CAN/Cluster IPC observations within deadline.

    Flush old frames, issue unique functional challenges after not_before, observe
    continuous process health, and preserve corrupt raw frames in the snapshot.
    Receiver and core share monotonic clock. Never manufacture a response echo.
    """
    def collect(self, bundle: VehicleBundleManifest, *, trial: bool,
                not_before: float, timeout_s: int) -> CompatibilitySnapshot: ...


@dataclass(frozen=True)
class PreparedArtifact:
    """Output of trusted independently signed release preparation.

    prepare_artifact verifies Ed25519 release signature, job/device/hardware/
    version/entrypoint binding, validity and anti-downgrade, safely extracts the
    archive and caches original manifest/signature/archive for HTTPS publication.
    Core independently checks exact target metadata and signed archive size/hash.
    Both outputs must be immutable/private until staging/publication completes.
    """
    ecu_id: str
    software_version: str
    entrypoint: str
    archive_path: Path
    staging_dir: Path


@dataclass(frozen=True)
class VehicleUpdateResult:
    transaction_id: str | None
    bundle_version: str | None
    phase: str
    last_error: dict | None
    evidence: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {"transaction_id": self.transaction_id, "bundle_version": self.bundle_version,
                "phase": self.phase, "last_error": self.last_error, "evidence": list(self.evidence)}


def _bundle_dict(bundle: VehicleBundleManifest) -> dict:
    return json.loads(bundle.canonical_bytes())


def _error(exc: Exception, default="COORDINATOR_IO_FAILED") -> dict:
    return {"code": exc.code if isinstance(exc, OtaError) else default,
            "message": exc.message if isinstance(exc, OtaError) else str(exc)}


class VehicleCoordinator:
    def __init__(self, *, state_path: Path, installer: ABSlotInstaller, zone: ZoneClient,
                 probe: CompatibilityProbe,
                 prepare_artifact: Callable[[EcuTarget, Any], PreparedArtifact],
                 stable_bundle: VehicleBundleManifest | None = None,
                 publish_artifacts: Callable[[VehicleBundleManifest, Mapping[str, PreparedArtifact]], None],
                 maintenance: Callable[[bool], None],
                 progress: Callable[[dict], None] | None = None,
                 validator: CompatibilityValidator | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 monotonic: Callable[[], float] = time.monotonic):
        self.state_path = Path(state_path).absolute()
        if (installer.service_unit != "central-control.service"
                or self.state_path.resolve().is_relative_to(installer.slots_dir.resolve())
                or self.state_path.resolve() in {installer.state_path.resolve(), installer.active_link.absolute()}):
            raise OtaError("COORDINATOR_CONFIG_INVALID", "coordinator must own Central application and independent journal")
        self.installer, self.zone, self.probe = installer, zone, probe
        self.prepare_artifact, self.publish_artifacts = prepare_artifact, publish_artifacts
        self.maintenance, self.progress = maintenance, progress
        self.validator = validator or CompatibilityValidator()
        self.now, self.monotonic = now, monotonic
        self._phase_deadline = None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state
        else:
            local = installer.state
            if local.phase != "stable" or local.completed_transactions or stable_bundle is None:
                raise OtaError("TRANSACTION_STATE_INVALID", "fresh journal requires known provisioned stable version set")
            stable_bundle = VehicleBundleManifest.from_bytes(stable_bundle.canonical_bytes())
            static = self.validator.validate_static(stable_bundle)
            if not static.passed:
                raise OtaError(static.errors[0]["code"], static.errors[0]["message"])
            VehicleTransactionState(stable_bundle=_bundle_dict(stable_bundle)).save_atomic(self.state_path)

    @property
    def state(self) -> VehicleTransactionState:
        return VehicleTransactionState.load(self.state_path)

    def _save(self, state: VehicleTransactionState) -> None:
        if state.phase in TERMINAL_STATES:
            result = VehicleUpdateResult(state.transaction_id, state.bundle_version,
                                         state.phase, state.last_error, tuple(state.evidence)).to_dict()
            state = replace(state, completed_results={**state.completed_results, state.transaction_id: result})
        state.save_atomic(self.state_path)

    def _transition(self, phase: str) -> None:
        state = self.state.transition(phase)
        if phase == "COMMITTED":
            # Promote vehicle metadata in the same durable write as terminal
            # commit; prior baseline remains available before that decision.
            ecus = {role: {**ecu, "stable_slot": ecu["trial_slot"], "active_slot": ecu["trial_slot"],
                           "trial_slot": None, "phase": "COMMITTED"}
                    for role, ecu in state.ecu_states.items()}
            state = replace(state, stable_bundle=state.current_bundle, ecu_states=ecus)
        self._phase_deadline = None
        if phase in {"PREPARING", "ACTIVATING", "VERIFYING", "RECOVERY_VERIFYING"}:
            policy = state.current_bundle["health_policy"]
            key = {"PREPARING": "prepare_timeout_s", "ACTIVATING": "activation_timeout_s",
                   "VERIFYING": "verification_timeout_s", "RECOVERY_VERIFYING": "verification_timeout_s"}[phase]
            self._phase_deadline = self.monotonic() + policy[key]
        timestamp = self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        history = dict(state.completed_transactions)
        if phase in TERMINAL_STATES:
            history[state.transaction_id] = phase
        event = {"schema_version": 1, "sequence": len(state.events) + 1,
                 "timestamp": timestamp, "transaction_id": state.transaction_id,
                 "bundle_version": state.bundle_version, "phase": phase,
                 "ecu_states": state.ecu_states, "last_error": state.last_error,
                 "evidence": state.evidence}
        self._save(replace(state, updated_at=timestamp, completed_transactions=history,
                           events=[*state.events, event]))
        if self.progress is not None:
            try:
                self.progress(event)
            except Exception:
                # Durable events are authoritative; status delivery cannot change
                # a successful commit or prevent mandatory recovery.
                pass

    def _result(self) -> VehicleUpdateResult:
        state = self.state
        return VehicleUpdateResult(state.transaction_id, state.bundle_version,
                                   state.phase, state.last_error, tuple(state.evidence))

    def _external(self, action: str, function: Callable, timeout_s: int):
        started = self.monotonic()
        deadline = started + timeout_s
        if self._phase_deadline is not None:
            deadline = min(deadline, self._phase_deadline)
        if started >= deadline:
            raise OtaError("ACTION_TIMEOUT", f"{action} has no remaining phase budget")
        state = self.state
        self._save(replace(state, attempts=state.attempts + 1,
                           evidence=[*state.evidence, {"check": "action", "action": action, "status": "intent"}]))
        result = function()
        elapsed = self.monotonic() - started
        if elapsed < 0 or self.monotonic() > deadline:
            raise OtaError("ACTION_TIMEOUT", f"{action} exceeded its monotonic deadline")
        return result

    def _ecu(self, role: str, **updates) -> None:
        state = self.state
        ecus = {**state.ecu_states, role: {**state.ecu_states.get(role, {}), **updates}}
        self._save(replace(state, ecu_states=ecus))

    def _set_maintenance(self, enabled: bool, timeout_s: int) -> None:
        # On intent survives callback interruption. Off intent keeps the flag
        # true until confirmed, so a terminal-state restart retries release.
        if enabled:
            self._save(replace(self.state, maintenance_enabled=True))
        self._external(f"maintenance:{'on' if enabled else 'off'}", lambda: self.maintenance(enabled), timeout_s)
        if not enabled:
            self._save(replace(self.state, maintenance_enabled=False))

    def _zone_action(self, name: str, expected: OtaStatus, timeout_s: int):
        state = self.state
        token = int(state.transaction_id[:8], 16)
        slot = Slot[state.ecu_states["digital-cluster"]["trial_slot"]]
        response_slot = Slot[state.ecu_states["digital-cluster"]["stable_slot"]] if name == "rollback" else slot
        self._ecu("digital-cluster", **{name: "intent"})
        result = self._external(f"cluster:{name}", lambda: getattr(self.zone, name)(
            state.transaction_id, token, slot, timeout_s), timeout_s)
        try:
            result.encode()
            if (result.status != expected or result.transaction_token != token or result.slot != response_slot or result.detail != 0):
                raise ValueError("status/token/slot/detail does not match command")
        except (ValueError, AttributeError, TypeError) as exc:
            raise OtaError("ZONE_STATUS_INVALID", str(exc)) from exc
        self._ecu("digital-cluster", **{name: "done", "phase": expected.name})
        if name == "activate":
            self._ecu("digital-cluster", active_slot=slot.name)
        return result

    def _verify(self, bundle: VehicleBundleManifest, *, trial: bool) -> bool:
        started = self.monotonic()
        snapshot = self._external("verify:trial" if trial else "verify:recovery", lambda:
            self.probe.collect(bundle, trial=trial, not_before=started,
                               timeout_s=bundle.health_policy.verification_timeout_s),
            bundle.health_policy.verification_timeout_s)
        if isinstance(snapshot, CompatibilitySnapshot) and snapshot.ended_at > self.monotonic():
            raise OtaError("OBSERVATION_WINDOW_INVALID", "receiver observation extends into future monotonic time")
        result = self.validator.validate_runtime(bundle, snapshot, trial=trial, not_before=started)
        state = self.state
        self._save(replace(state, evidence=[*state.evidence,
            {"check": "verification", "scope": "trial" if trial else "recovery", **result.to_dict()}]))
        if not result.passed:
            first = result.errors[0]
            self._save(replace(self.state, last_error={"code": first["code"], "message": first["message"]}))
        return result.passed

    def _check_artifact(self, target: EcuTarget, prepared: PreparedArtifact) -> None:
        if (not isinstance(prepared, PreparedArtifact)
                or (prepared.ecu_id, prepared.software_version, prepared.entrypoint)
                != (target.ecu_id, target.software_version, target.entrypoint)):
            raise OtaError("ARTIFACT_BINDING_INVALID", "verified release does not bind the target")
        archive = Path(prepared.archive_path)
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != target.artifact_size:
            raise OtaError("ARTIFACT_SIZE_MISMATCH", "cached release has incorrect signed size")
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != target.artifact_sha256:
            raise OtaError("ARTIFACT_HASH_MISMATCH", "cached release differs from signed SHA-256")

    def _abort(self, error: dict) -> VehicleUpdateResult:
        self._save(replace(self.state, last_error=error))
        if self.state.phase != "ABORTED":
            self._transition("ABORTED")  # Durable abort before staging cleanup.
        state = self.state
        failures = []
        cluster = state.ecu_states.get("digital-cluster", {})
        if cluster.get("prepare") in {"intent", "done"}:
            try:
                bundle = VehicleBundleManifest.from_bytes(json.dumps(state.current_bundle).encode())
                self._zone_action("rollback", OtaStatus.ROLLED_BACK, bundle.health_policy.activation_timeout_s)
            except Exception as exc:
                failures.append(_error(exc))
        try:
            local = self.installer.state
            if (local.transaction_id == state.transaction_id
                    or (local.completed_transactions.get(state.transaction_id) == "rolled_back"
                        and state.ecu_states.get("central-control", {}).get("rollback") != "done")):
                self._ecu("central-control", rollback="intent")
                self._external("central:abort-staging", lambda: self.installer.rollback(state.transaction_id), 30)
                self._external("central:cleanup-staging", self.installer.recover_on_startup, 30)
                self._ecu("central-control", rollback="done", phase="ROLLED_BACK")
        except Exception as exc:
            failures.append(_error(exc))
        if failures:
            self._save(replace(self.state, evidence=[*self.state.evidence,
                {"check": "abort_cleanup", "passed": False, "errors": failures}]))
        return self._result()

    def execute(self, bundle: VehicleBundleManifest, artifacts: Mapping[str, Any]) -> VehicleUpdateResult:
        # Re-parse even manually constructed dataclasses; the runtime provides
        # separately verified bundle signature before entering this core API.
        bundle = VehicleBundleManifest.from_bytes(bundle.canonical_bytes())
        digest = hashlib.sha256(bundle.canonical_bytes()).hexdigest()
        state = self.state.retain_transaction(bundle.transaction_id, digest)
        if bundle.transaction_id in state.completed_transactions:
            if state.transaction_id == bundle.transaction_id:
                return self._result()
            recorded = state.completed_results.get(bundle.transaction_id)
            if recorded is not None:
                return VehicleUpdateResult(recorded["transaction_id"], recorded["bundle_version"], recorded["phase"],
                                           recorded["last_error"], tuple(recorded["evidence"]))
            raise OtaError("TRANSACTION_STATE_INVALID", "completed transaction lacks its durable result")
        if state.phase == "RECOVERY_FAILED":
            raise OtaError("RECOVERY_REQUIRED", "failed recovery requires manual intervention")
        if state.phase != "IDLE" and state.phase not in TERMINAL_STATES:
            raise OtaError("RECOVERY_REQUIRED", "pending transaction must recover before execute")
        if self.installer.state.phase != "stable":
            raise OtaError("RECOVERY_REQUIRED", "local pending slot requires recovery")
        state = replace(state, phase="IDLE", transaction_id=bundle.transaction_id,
                        current_bundle=_bundle_dict(bundle), bundle_version=bundle.bundle_version,
                        ecu_states={}, attempts=0, last_error=None, evidence=[], events=[])
        self._save(state)
        self._transition("PREPARING")
        policy = bundle.health_policy
        try:
            bundle.validate_at(self.now())
            static = self.validator.validate_static(bundle)
            self._save(replace(self.state, evidence=[*self.state.evidence, {"check": "static", **static.to_dict()}]))
            if not static.passed:
                raise OtaError(static.errors[0]["code"], static.errors[0]["message"])
            if set(artifacts) != {target.ecu_id for target in bundle.targets}:
                raise OtaError("ARTIFACT_SET_INVALID", "both and only both target artifacts are required")
            prepared = {}
            for target in bundle.targets:
                item = self._external(f"cache:{target.ecu_id}", lambda target=target:
                    self.prepare_artifact(target, artifacts[target.ecu_id]), policy.prepare_timeout_s)
                self._check_artifact(target, item)
                prepared[target.ecu_id] = item
            # Entire signed artifact set verified before touching either slot.
            self._external("publish-artifacts", lambda: self.publish_artifacts(bundle, prepared), policy.prepare_timeout_s)
            remote = self._external("cluster:status", lambda: self.zone.status(None, 0, policy.prepare_timeout_s), policy.prepare_timeout_s)
            remote.encode()
            if remote.status not in {OtaStatus.IDLE, OtaStatus.COMMITTED, OtaStatus.ROLLED_BACK} or remote.detail or remote.transaction_token != 0:
                raise OtaError("ZONE_NOT_STABLE", "remote application has a pending or failed transaction")
            local = self.installer.state
            for role, stable in (("central-control", local.stable_slot), ("digital-cluster", remote.slot.name)):
                self._ecu(role, stable_slot=stable, active_slot=stable,
                          trial_slot="B" if stable == "A" else "A", phase="STABLE")
            target = next(t for t in bundle.targets if t.ecu_id == "central-control")
            self._ecu("central-control", stage="intent")
            self._external("central:stage", lambda: self.installer.stage(bundle.transaction_id,
                prepared[target.ecu_id].staging_dir, target.entrypoint, target.software_version), policy.prepare_timeout_s)
            self._ecu("central-control", stage="done", phase="READY")
            self._zone_action("prepare", OtaStatus.READY, policy.prepare_timeout_s)
            self._transition("READY")
        except Exception as exc:
            return self._abort(_error(exc))
        try:
            return self._activate_verify_commit(bundle)
        except Exception as exc:
            # An already terminal commit is never undone because status or
            # maintenance publication failed after its durable decision.
            if self.state.phase == "COMMITTED":
                self._save(replace(self.state, last_error=_error(exc)))
                return self._result()
            if not (isinstance(exc, OtaError) and exc.code == "COMPATIBILITY_FAILED"):
                self._save(replace(self.state, last_error=_error(exc)))
            return self._rollback()

    def _activate_verify_commit(self, bundle: VehicleBundleManifest) -> VehicleUpdateResult:
        policy = bundle.health_policy
        self._transition("ACTIVATING")
        self._set_maintenance(True, policy.activation_timeout_s)
        self._zone_action("activate", OtaStatus.VERIFYING, policy.activation_timeout_s)
        self._ecu("central-control", activate="intent")
        self._external("central:activate", lambda: self.installer.activate_trial(bundle.transaction_id,
            rollback_on_failure=False), policy.activation_timeout_s)
        self._ecu("central-control", activate="done", phase="VERIFYING", active_slot=self.installer.state.active_slot)
        self._transition("VERIFYING")
        if not self._verify(bundle, trial=True):
            raise OtaError("COMPATIBILITY_FAILED", "trial verification failed")
        self._zone_action("commit", OtaStatus.COMMITTED, policy.activation_timeout_s)
        self._ecu("central-control", commit="intent")
        self._external("central:commit", lambda: self.installer.commit(bundle.transaction_id), policy.activation_timeout_s)
        self._ecu("central-control", commit="done", phase="COMMITTED", active_slot=self.installer.state.active_slot)
        self._transition("COMMITTED")
        self._set_maintenance(False, policy.activation_timeout_s)
        return self._result()

    def _rollback(self) -> VehicleUpdateResult:
        state = self.state
        if state.phase in {"ACTIVATING", "VERIFYING"}:
            self._transition("ROLLING_BACK")
        elif state.phase != "ROLLING_BACK":
            raise OtaError("RECOVERY_REQUIRED", "rollback requires durable activation/recovery intent")
        state = self.state
        bundle = VehicleBundleManifest.from_bytes(json.dumps(state.current_bundle).encode())
        failures = []
        # ROLLING_BACK is fully fsynced before first command; Cluster rollback
        # attempt always precedes restarting the local Central application.
        try:
            self._zone_action("rollback", OtaStatus.ROLLED_BACK, bundle.health_policy.activation_timeout_s)
            stable = self.state.ecu_states["digital-cluster"]["stable_slot"]
            self._ecu("digital-cluster", active_slot=stable, phase="ROLLED_BACK")
        except Exception as exc:
            failures.append(_error(exc))
            self._ecu("digital-cluster", rollback="failed", error=_error(exc))
        try:
            self._ecu("central-control", rollback="intent")
            self._external("central:rollback", lambda: self.installer.rollback(state.transaction_id),
                           bundle.health_policy.activation_timeout_s)
            self._ecu("central-control", rollback="done", phase="ROLLED_BACK",
                      active_slot=self.installer.state.active_slot)
        except Exception as exc:
            failures.append(_error(exc))
            self._ecu("central-control", rollback="failed", error=_error(exc))
        state = self.state
        self._save(replace(state, evidence=[*state.evidence,
            {"check": "rollback", "passed": not failures, "errors": failures}]))
        self._transition("RECOVERY_VERIFYING")
        return self._recovery_verify()

    def _recovery_verify(self) -> VehicleUpdateResult:
        state = self.state
        operations_passed = all(state.ecu_states.get(role, {}).get("rollback") == "done"
                                for role in ("central-control", "digital-cluster"))
        passed = False
        try:
            if state.stable_bundle is None:
                raise OtaError("RECOVERY_BASELINE_UNKNOWN", "prior stable version set is unavailable")
            stable = VehicleBundleManifest.from_bytes(json.dumps(state.stable_bundle).encode())
            passed = self._verify(stable, trial=False)
        except Exception as exc:
            self._save(replace(self.state, last_error=_error(exc), evidence=[*self.state.evidence,
                {"check": "verification", "scope": "recovery", "passed": False, "errors": [_error(exc)]}]))
        if not operations_passed:
            self._save(replace(self.state, last_error={"code": "ROLLBACK_FAILED",
                "message": "at least one participant failed to confirm prior stable slot; see rollback evidence"}))
        success = passed and operations_passed
        self._transition("ROLLED_BACK" if success else "RECOVERY_FAILED")
        if success:
            try:
                self._set_maintenance(False, stable.health_policy.activation_timeout_s)
            except Exception as exc:
                self._save(replace(self.state, last_error=_error(exc)))
        return self._result()

    def recover_on_startup(self) -> VehicleUpdateResult:
        """Explicit idempotent startup recovery; never resumes activation.

        An interrupted VERIFYING record, including partial commit, attempts
        whole-bundle rollback. Existing participants cannot undo a completed
        stable promotion; its rejection is recorded as RECOVERY_FAILED.
        """
        state = self.state
        if state.phase in {"COMMITTED", "ROLLED_BACK"} and state.maintenance_enabled:
            bundle = VehicleBundleManifest.from_bytes(json.dumps(state.current_bundle).encode())
            try:
                self._set_maintenance(False, bundle.health_policy.activation_timeout_s)
            except Exception as exc:
                self._save(replace(self.state, last_error=_error(exc)))
            return self._result()
        if state.phase in {"PREPARING", "READY"}:
            return self._abort({"code": "UPDATE_INTERRUPTED", "message": "pre-activation transaction aborted on startup"})
        if state.phase in {"ACTIVATING", "VERIFYING", "ROLLING_BACK"}:
            if state.last_error is None:
                self._save(replace(state, last_error={"code": "UPDATE_INTERRUPTED", "message": "uncommitted update interrupted"}))
            return self._rollback()
        if state.phase == "RECOVERY_VERIFYING":
            return self._recovery_verify()
        if state.phase == "ABORTED" and (self.installer.state.transaction_id == state.transaction_id
                or (state.ecu_states.get("central-control", {}).get("stage") in {"intent", "done"}
                    and state.ecu_states["central-control"].get("rollback") != "done")
                or (state.ecu_states.get("digital-cluster", {}).get("prepare") in {"intent", "done"}
                    and state.ecu_states["digital-cluster"].get("rollback") != "done")):
            return self._abort(state.last_error or {"code": "UPDATE_INTERRUPTED", "message": "resume staging cleanup"})
        return self._result()
