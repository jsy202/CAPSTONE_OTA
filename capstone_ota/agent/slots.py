"""Durable application A/B slots, independent of the legacy release installer.

The active-slot symlink selects the application. state.json is a strict v1
journal; pending phase intent is durable before changing application selection.
One process owns an install root; callers must serialize lifecycle commands.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from capstone_ota.common.errors import OtaError

from .service_manager import ServiceManager


_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
_PHASES = {"stable", "staging", "staged", "activating", "trial", "committing", "rolling_back"}


def _transaction_id(value: str | UUID) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise OtaError("SLOT_TRANSACTION_INVALID", "transaction ID must be a UUID") from exc


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _entrypoint(entrypoint: str) -> PurePosixPath:
    if not isinstance(entrypoint, str) or not entrypoint:
        raise OtaError("SLOT_INVALID", "entrypoint must be a normalized relative path")
    relative = PurePosixPath(entrypoint)
    if relative.is_absolute() or relative.as_posix() != entrypoint or ".." in relative.parts:
        raise OtaError("SLOT_INVALID", "entrypoint must be a normalized relative path")
    return relative


def _tree_digest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise OtaError("SLOT_INVALID", "application must be a real directory")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise OtaError("SLOT_INVALID", "application contains unsupported file types")
        digest.update(b"D" if stat.S_ISDIR(mode) else b"F")
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update((mode & 0o777).to_bytes(2, "big"))
        if stat.S_ISREG(mode):
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


def _sync_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        if path.is_dir():
            directories.append(path)
        else:
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _sync_directory(path)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate state field")
        result[key] = value
    return result


@dataclass(frozen=True)
class SlotState:
    """Strict v1 journal. Completed UUID outcomes prevent delayed command replays.

    stable_version is unknown (None) for the provisioned initial A application.
    Pending transaction metadata exists in every non-stable phase. active_slot
    describes the last persisted selection; recovery also inspects active-slot.
    """

    schema_version: int = 1
    stable_slot: str = "A"
    active_slot: str = "A"
    trial_slot: str | None = None
    phase: str = "stable"
    transaction_id: str | None = None
    stable_version: str | None = None
    trial_version: str | None = None
    trial_entrypoint: str | None = None
    trial_digest: str | None = None
    completed_transactions: dict[str, str] = field(default_factory=dict)

    def _validate(self) -> None:
        invalid = OtaError("SLOT_STATE_INVALID", "invalid v1 slot state")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise invalid
        if self.stable_slot not in {"A", "B"} or self.active_slot not in {"A", "B"}:
            raise invalid
        if self.phase not in _PHASES or not isinstance(self.completed_transactions, dict):
            raise invalid
        for tx, outcome in self.completed_transactions.items():
            if not isinstance(tx, str) or _transaction_id(tx) != tx or outcome not in {"committed", "rolled_back"}:
                raise invalid
        if self.stable_version is not None and (not isinstance(self.stable_version, str) or not _VERSION.fullmatch(self.stable_version)):
            raise invalid
        pending = (self.trial_slot, self.transaction_id, self.trial_version, self.trial_entrypoint, self.trial_digest)
        if self.phase == "stable":
            if any(value is not None for value in pending) or self.active_slot != self.stable_slot:
                raise invalid
            return
        if self.trial_slot not in {"A", "B"} or self.trial_slot == self.stable_slot:
            raise invalid
        if not isinstance(self.transaction_id, str) or _transaction_id(self.transaction_id) != self.transaction_id:
            raise invalid
        if self.transaction_id in self.completed_transactions:
            raise invalid
        if not isinstance(self.trial_version, str) or not _VERSION.fullmatch(self.trial_version):
            raise invalid
        _entrypoint(self.trial_entrypoint)
        if not isinstance(self.trial_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", self.trial_digest):
            raise invalid
        if self.phase in {"staging", "staged", "activating"} and self.active_slot != self.stable_slot:
            raise invalid
        if self.phase in {"trial", "committing"} and self.active_slot != self.trial_slot:
            raise invalid

    @classmethod
    def load(cls, path: Path) -> SlotState:
        try:
            data = json.loads(Path(path).read_text(), object_pairs_hook=_unique_fields)
            if not isinstance(data, dict) or set(data) != {item.name for item in fields(cls)}:
                raise ValueError("unknown or missing fields")
            state = cls(**data)
            state._validate()
            return state
        except (OSError, ValueError, TypeError, OtaError) as exc:
            raise OtaError("SLOT_STATE_INVALID", "cannot load valid v1 slot state") from exc

    def save_atomic(self, path: Path) -> None:
        self._validate()
        path = Path(path)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(asdict(self), stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


class ABSlotInstaller:
    def __init__(
        self,
        install_root: Path,
        service_manager: ServiceManager,
        service_unit: str = "digital-dash.service",
        health_timeout: int = 15,
        state_path: Path | None = None,
    ):
        if health_timeout <= 0:
            raise ValueError("health timeout must be positive")
        self.install_root = Path(install_root).absolute()
        self.slots_dir = self.install_root / "slots"
        self.active_link = self.install_root / "active-slot"
        self.state_path = Path(state_path) if state_path is not None else self.install_root / "state.json"
        if self.state_path.resolve().is_relative_to(self.slots_dir.resolve()) or self.state_path.resolve() == self.active_link.absolute():
            raise OtaError("SLOT_INVALID", "slot journal must be outside replaceable application slots")
        self.service_manager = service_manager
        self.service_unit = service_unit
        self.health_timeout = health_timeout
        self.install_root.mkdir(parents=True, exist_ok=True)
        if self.install_root.is_symlink() or self.slots_dir.is_symlink():
            raise OtaError("SLOT_INVALID", "installation directories must not be symlinks")
        self.slots_dir.mkdir(exist_ok=True)
        self._check_slot_paths()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            self.state  # Reject corruption without changing selector or metadata.
            return
        if self.active_link.exists() or self.active_link.is_symlink():
            raise OtaError("SLOT_STATE_INVALID", "existing selector has no slot journal")
        (self.slots_dir / "A").mkdir(exist_ok=True)
        _sync_directory(self.slots_dir)
        _sync_directory(self.install_root)
        _sync_directory(self.install_root.parent)
        SlotState().save_atomic(self.state_path)
        self._select("A")

    @property
    def state(self) -> SlotState:
        return SlotState.load(self.state_path)

    def _check_slot_paths(self) -> None:
        if self.slots_dir.is_symlink() or not self.slots_dir.is_dir():
            raise OtaError("SLOT_INVALID", "slot root must be a real directory")
        for slot in ("A", "B"):
            path = self.slots_dir / slot
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise OtaError("SLOT_INVALID", "application slots must be real directories")

    def _select(self, slot: str) -> None:
        self._check_slot_paths()
        if slot not in {"A", "B"} or not (self.slots_dir / slot).is_dir():
            raise OtaError("SLOT_INVALID", "selected application slot is missing")
        temporary = self.install_root / f".active-slot.{uuid4().hex}"
        try:
            temporary.symlink_to(Path("slots") / slot)
            os.replace(temporary, self.active_link)
            _sync_directory(self.install_root)
        finally:
            temporary.unlink(missing_ok=True)

    def stage(self, transaction_id: str | UUID, staging_dir: Path, entrypoint: str, version: str) -> SlotState:
        tx = _transaction_id(transaction_id)
        state = self.state
        if tx in state.completed_transactions:
            return state
        if state.transaction_id is not None and state.transaction_id != tx:
            raise OtaError("SLOT_BUSY", "another transaction owns the trial slot")
        self._check_slot_paths()
        if not (self.slots_dir / state.stable_slot).is_dir():
            raise OtaError("SLOT_INVALID", "stable application slot is missing")
        if self._selected_slot() != state.active_slot:
            raise OtaError("SLOT_RECOVERY_REQUIRED", "journal and active selector disagree")
        source = Path(staging_dir)
        resolved_source, resolved_slots = source.resolve(), self.slots_dir.resolve()
        if (resolved_source.is_relative_to(resolved_slots)
                or resolved_slots.is_relative_to(resolved_source)
                or self.state_path.resolve().is_relative_to(resolved_source)):
            raise OtaError("SLOT_INVALID", "staging source overlaps managed slots or metadata")
        relative = _entrypoint(entrypoint)
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise OtaError("SLOT_INVALID", "invalid application version")
        digest = _tree_digest(source)
        executable = source.joinpath(*relative.parts)
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise OtaError("SLOT_INVALID", "entrypoint is missing or not executable")
        if state.transaction_id == tx:
            if (state.trial_version, state.trial_entrypoint, state.trial_digest) != (version, entrypoint, digest):
                raise OtaError("SLOT_CONFLICT", "transaction already has different application content")
            if state.phase == "staging":
                raise OtaError("SLOT_RECOVERY_REQUIRED", "interrupted staging requires recovery")
            return state
        target_slot = "B" if state.stable_slot == "A" else "A"
        state = replace(state, phase="staging", transaction_id=tx, trial_slot=target_slot,
                        trial_version=version, trial_entrypoint=entrypoint, trial_digest=digest)
        state.save_atomic(self.state_path)
        temporary = self.slots_dir / f".staging-{tx}"
        backup = self.slots_dir / f".backup-{tx}"
        target = self.slots_dir / target_slot
        try:
            shutil.copytree(source, temporary)
            if _tree_digest(temporary) != digest:
                raise OtaError("SLOT_CONFLICT", "application changed while staging")
            _sync_tree(temporary)
            _sync_directory(self.slots_dir)
            if target.exists():
                os.replace(target, backup)
                _sync_directory(self.slots_dir)
            os.replace(temporary, target)
            _sync_directory(self.slots_dir)
            state = replace(state, phase="staged")
            state.save_atomic(self.state_path)
            if backup.exists():
                shutil.rmtree(backup)
                _sync_directory(self.slots_dir)
            return state
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
                _sync_directory(self.slots_dir)

    def _selected_slot(self) -> str | None:
        if not self.active_link.is_symlink():
            if self.active_link.exists():
                raise OtaError("SLOT_STATE_INVALID", "active selector must be a symlink")
            return None
        target = os.readlink(self.active_link)
        if target not in {"slots/A", "slots/B"}:
            raise OtaError("SLOT_STATE_INVALID", "active selector escapes application slots")
        return Path(target).name

    def _pending(self, transaction_id: str | UUID) -> tuple[str, SlotState]:
        tx = _transaction_id(transaction_id)
        state = self.state
        if tx not in state.completed_transactions and state.transaction_id != tx:
            raise OtaError("SLOT_TRANSACTION_MISMATCH", "transaction does not own the trial slot")
        return tx, state

    def _restart(self) -> bool:
        return self.service_manager.restart_and_wait_healthy(self.service_unit, self.health_timeout)

    def activate_trial(self, transaction_id: str | UUID) -> SlotState:
        tx, state = self._pending(transaction_id)
        if tx in state.completed_transactions:
            return state
        if state.phase == "trial":
            if self._selected_slot() != state.trial_slot:
                raise OtaError("SLOT_RECOVERY_REQUIRED", "trial journal and active selector disagree")
            return state
        if state.phase != "staged":
            raise OtaError("SLOT_RECOVERY_REQUIRED", "trial activation requires a fully staged application")
        target = self.slots_dir / state.trial_slot
        if _tree_digest(target) != state.trial_digest:
            raise OtaError("SLOT_CONFLICT", "staged application content changed")
        state = replace(state, phase="activating")
        state.save_atomic(self.state_path)
        self._select(state.trial_slot)
        if not self._restart():
            self.rollback(tx)
            raise OtaError("HEALTH_CHECK_FAILED", "trial application failed its health check")
        state = replace(state, phase="trial", active_slot=state.trial_slot)
        state.save_atomic(self.state_path)
        return state

    def _finish(self, state: SlotState, outcome: str) -> SlotState:
        history = dict(state.completed_transactions)
        history[state.transaction_id] = outcome
        committed = outcome == "committed"
        slot = state.trial_slot if committed else state.stable_slot
        state = replace(state, stable_slot=slot, active_slot=slot,
                        stable_version=state.trial_version if committed else state.stable_version,
                        phase="stable", transaction_id=None, trial_slot=None,
                        trial_version=None, trial_entrypoint=None, trial_digest=None,
                        completed_transactions=history)
        state.save_atomic(self.state_path)
        return state

    def commit(self, transaction_id: str | UUID) -> SlotState:
        tx, state = self._pending(transaction_id)
        if tx in state.completed_transactions:
            if state.completed_transactions[tx] != "committed":
                raise OtaError("SLOT_TRANSACTION_FINISHED", "rolled-back transaction cannot commit")
            return state
        if state.phase != "trial" or self._selected_slot() != state.trial_slot:
            raise OtaError("SLOT_NOT_TRIAL", "only an active trial can commit")
        state = replace(state, phase="committing")
        state.save_atomic(self.state_path)
        return self._finish(state, "committed")

    def rollback(self, transaction_id: str | UUID) -> SlotState:
        tx, state = self._pending(transaction_id)
        if tx in state.completed_transactions:
            if state.completed_transactions[tx] != "rolled_back":
                raise OtaError("SLOT_TRANSACTION_FINISHED", "committed transaction cannot roll back")
            return state
        restart = state.phase in {"activating", "trial", "committing", "rolling_back"} or self._selected_slot() != state.stable_slot
        state = replace(state, phase="rolling_back")
        state.save_atomic(self.state_path)
        self._select(state.stable_slot)
        if restart and not self._restart():
            raise OtaError("ROLLBACK_FAILED", "stable application failed its health check")
        return self._finish(state, "rolled_back")

    def recover_on_startup(self) -> SlotState:
        """Abort pending updates, including activation lacking a final trial write.

        A durable stable promotion is the commit point. A committing intent
        alone is not a commit, so it restores the previous stable application.
        Recovery never infers a stable version from slot directory contents.
        """
        self._check_slot_paths()
        state = self.state
        if not (self.slots_dir / state.stable_slot).is_dir():
            raise OtaError("SLOT_INVALID", "stable application slot is missing")
        if state.phase != "stable":
            state = self.rollback(state.transaction_id)
        elif self._selected_slot() != state.stable_slot:
            raise OtaError("SLOT_STATE_INVALID", "stable journal and active selector disagree")
        # Also handles interruption after finishing rollback, before cleanup.
        changed = False
        for tx in state.completed_transactions:
            for prefix in (".staging-", ".backup-"):
                artifact = self.slots_dir / f"{prefix}{tx}"
                if artifact.is_symlink():
                    artifact.unlink()
                    changed = True
                elif artifact.exists():
                    shutil.rmtree(artifact)
                    changed = True
        if changed:
            _sync_directory(self.slots_dir)
        return state
