"""Strict v1 vehicle journal. One serialized coordinator owns each journal.

The graph is the design's exact graph. Starting a subsequent transaction creates
a new IDLE record retaining stable metadata, UUID outcomes and token bindings.
Terminal states have no outgoing edges. Every side effect must follow save_atomic.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from uuid import UUID

from capstone_ota.common.errors import OtaError
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest


STATE_GRAPH = {
    "IDLE": frozenset({"PREPARING"}),
    "PREPARING": frozenset({"READY", "ABORTED"}),
    "READY": frozenset({"ACTIVATING", "ABORTED"}),
    "ACTIVATING": frozenset({"VERIFYING", "ROLLING_BACK"}),
    "VERIFYING": frozenset({"COMMITTED", "ROLLING_BACK"}),
    "ROLLING_BACK": frozenset({"RECOVERY_VERIFYING"}),
    "RECOVERY_VERIFYING": frozenset({"ROLLED_BACK", "RECOVERY_FAILED"}),
    "COMMITTED": frozenset(), "ABORTED": frozenset(),
    "ROLLED_BACK": frozenset(), "RECOVERY_FAILED": frozenset(),
}
TERMINAL_STATES = frozenset({"COMMITTED", "ABORTED", "ROLLED_BACK", "RECOVERY_FAILED"})


def transaction_token(transaction_id: str) -> int:
    try:
        if not isinstance(transaction_id, str) or str(UUID(transaction_id)) != transaction_id:
            raise ValueError("noncanonical UUID")
        return int.from_bytes(UUID(transaction_id).bytes[:4], "big")
    except (ValueError, TypeError, AttributeError) as exc:
        raise OtaError("TRANSACTION_STATE_INVALID", "transaction ID must be a canonical UUID") from exc


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


@dataclass(frozen=True)
class VehicleTransactionState:
    schema_version: int = 1
    phase: str = "IDLE"
    transaction_id: str | None = None
    bundle_version: str | None = None
    current_bundle: dict | None = None
    stable_bundle: dict | None = None
    ecu_states: dict = field(default_factory=dict)
    attempts: int = 0
    maintenance_enabled: bool = False
    last_error: dict | None = None
    evidence: list[dict] = field(default_factory=list)
    updated_at: str | None = None
    events: list[dict] = field(default_factory=list)
    token_history: dict[str, str] = field(default_factory=dict)
    bundle_history: dict[str, str] = field(default_factory=dict)
    completed_transactions: dict[str, str] = field(default_factory=dict)
    completed_results: dict[str, dict] = field(default_factory=dict)

    def transition(self, phase: str) -> VehicleTransactionState:
        if phase not in STATE_GRAPH.get(self.phase, ()):
            raise OtaError("TRANSACTION_TRANSITION_INVALID", f"cannot transition {self.phase} to {phase}")
        return replace(self, phase=phase)

    def retain_transaction(self, tx: str, bundle_digest: str) -> VehicleTransactionState:
        token = f"{transaction_token(tx):08x}"
        if self.token_history.get(token, tx) != tx:
            raise OtaError("TRANSACTION_TOKEN_COLLISION", "token already belongs to another retained UUID")
        if self.bundle_history.get(tx, bundle_digest) != bundle_digest:
            raise OtaError("TRANSACTION_CONFLICT", "UUID already binds a different signed bundle")
        if not isinstance(bundle_digest, str) or not re.fullmatch("[0-9a-f]{64}", bundle_digest):
            raise OtaError("TRANSACTION_STATE_INVALID", "bundle digest must be SHA-256")
        return replace(self, token_history={**self.token_history, token: tx},
                       bundle_history={**self.bundle_history, tx: bundle_digest})

    def _validate(self) -> None:
        invalid = OtaError("TRANSACTION_STATE_INVALID", "invalid v1 vehicle journal")
        if (type(self.schema_version) is not int or self.schema_version != 1
                or not isinstance(self.phase, str) or self.phase not in STATE_GRAPH
                or type(self.attempts) is not int or self.attempts < 0
                or type(self.maintenance_enabled) is not bool):
            raise invalid
        for value in (self.ecu_states, self.token_history, self.bundle_history,
                      self.completed_transactions, self.completed_results):
            if not isinstance(value, dict):
                raise invalid
        for token, tx in self.token_history.items():
            if token != f"{transaction_token(tx):08x}":
                raise invalid
        if set(self.token_history.values()) != set(self.bundle_history):
            raise invalid
        for tx, digest in self.bundle_history.items():
            transaction_token(tx)
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise invalid
        for tx, outcome in self.completed_transactions.items():
            if tx not in self.bundle_history or outcome not in TERMINAL_STATES:
                raise invalid
        for tx, result in self.completed_results.items():
            if (not isinstance(result, dict) or set(result) != {"transaction_id", "bundle_version", "phase", "last_error", "evidence"}
                    or result["transaction_id"] != tx or result["phase"] != self.completed_transactions.get(tx)
                    or not isinstance(result["bundle_version"], str) or not isinstance(result["evidence"], list)
                    or (result["last_error"] is not None and not isinstance(result["last_error"], dict))):
                raise invalid
        if self.last_error is not None and (not isinstance(self.last_error, dict)
                or set(self.last_error) != {"code", "message"}
                or any(not isinstance(v, str) for v in self.last_error.values())):
            raise invalid
        for value in (self.evidence, self.events):
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise invalid
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise invalid
        for value in (self.current_bundle, self.stable_bundle):
            if value is not None:
                VehicleBundleManifest.from_bytes(json.dumps(value).encode())
        roles = {"central-control", "digital-cluster"}
        if not set(self.ecu_states).issubset(roles):
            raise invalid
        for ecu in self.ecu_states.values():
            if (not isinstance(ecu, dict) or not {"stable_slot", "active_slot", "trial_slot", "phase"}.issubset(ecu)
                    or not set(ecu).issubset({"stable_slot", "active_slot", "trial_slot", "phase",
                                             "stage", "prepare", "activate", "commit", "rollback", "error"})
                    or ecu["stable_slot"] not in {"A", "B"} or ecu["active_slot"] not in {"A", "B"}
                    or ecu["trial_slot"] not in {"A", "B", None}
                    or ecu["phase"] not in {"STABLE", "READY", "VERIFYING", "COMMITTED", "ROLLED_BACK"}):
                raise invalid
            for action in {"stage", "prepare", "activate", "commit", "rollback"} & ecu.keys():
                if ecu[action] not in {"intent", "done", "failed"}:
                    raise invalid
            if "error" in ecu and (not isinstance(ecu["error"], dict) or set(ecu["error"]) != {"code", "message"}):
                raise invalid
        if self.phase not in {"IDLE", "PREPARING", "ABORTED"} and set(self.ecu_states) != roles:
            raise invalid
        if self.transaction_id is not None:
            transaction_token(self.transaction_id)
            if self.transaction_id not in self.bundle_history:
                raise invalid
            if (self.current_bundle is None or self.current_bundle["transaction_id"] != self.transaction_id
                    or self.current_bundle["bundle_version"] != self.bundle_version):
                raise invalid
            canonical = VehicleBundleManifest.from_bytes(json.dumps(self.current_bundle).encode()).canonical_bytes()
            if hashlib.sha256(canonical).hexdigest() != self.bundle_history[self.transaction_id] or self.stable_bundle is None:
                raise invalid
            if self.phase not in TERMINAL_STATES and self.transaction_id in self.completed_transactions:
                raise invalid
        elif self.phase != "IDLE" or self.current_bundle is not None or self.bundle_version is not None:
            raise invalid
        if self.phase in TERMINAL_STATES and self.completed_transactions.get(self.transaction_id) != self.phase:
            raise invalid
        if self.phase == "COMMITTED" and self.stable_bundle != self.current_bundle:
            raise invalid
        # No NaN/Infinity or non-JSON diagnostics may enter durable/MQTT evidence.
        json.dumps(asdict(self), allow_nan=False)

    @classmethod
    def load(cls, path: Path) -> VehicleTransactionState:
        try:
            data = json.loads(Path(path).read_text(), object_pairs_hook=_pairs)
            if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
                raise ValueError("unknown or missing journal fields")
            state = cls(**data)
            state._validate()
            return state
        except (OSError, ValueError, TypeError, OtaError) as exc:
            raise OtaError("TRANSACTION_STATE_INVALID", "cannot load valid v1 vehicle journal") from exc

    def save_atomic(self, path: Path) -> None:
        self._validate()
        path = Path(path)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(asdict(self), stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)
