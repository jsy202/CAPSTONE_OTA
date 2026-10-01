"""CAN-controlled Cluster application OTA, using a fixed Central HTTPS root.

Endpoint v1: /transactions/<eight-lowercase-hex-token>/cluster/ contains
transaction.json {schema_version:1, transaction_id:<canonical UUID>,
release_job_id:<canonical UUID>}, manifest.json (signed ReleaseManifest),
manifest.sig (raw Ed25519), artifact.tar.gz (signed size/SHA-256). The original
signed artifact_url is preserved; the archive is fetched from Central's root.
The transaction binding is authenticated by TLS to the configured Central.

The token journal is distinct from the slot installer's journal. One serialized
poll/command owner must operate each agent. Callers schedule heartbeats and own
transport open/close; this module does not configure the CAN network interface.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID

from capstone_ota.common.can_protocol import (
    ApplicationState, CanFrame, FunctionalTestRequestFrame, FunctionalTestResultFrame,
    Gear, HeartbeatFrame, OtaCommand, OtaCommandFrame, OtaStatus, OtaStatusFrame, Slot,
    VehicleStatusFrame, is_counter_contiguous,
)
from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import verify_manifest_signature
from capstone_ota.common.socketcan import CanTransport

from .archive import ArchiveLimits, safe_extract_tar
from .downloader import download_https
from .slots import ABSlotInstaller
from .updater import fetch_https_bytes
from .zonal_config import ZonalAgentConfig, unique_fields


class ZonalDetail(IntEnum):
    NONE = 0
    REJECTED = 1
    STALE = 2
    TOKEN = 3
    SLOT = 4
    UPDATE = 5
    STATE = 6


@dataclass(frozen=True)
class ApplicationObservation:
    """Values interpreted/displayed by the application, in wire signal units."""
    speed: int
    rpm: int
    gear: Gear
    warnings: int


class ClusterApplicationAdapter(Protocol):
    def apply_vehicle_status(self, sample: VehicleStatusFrame) -> None: ...

    def observe_functional_test(self, request: FunctionalTestRequestFrame) -> ApplicationObservation: ...


def _uuid(value: object) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("expected canonical UUID")
    return value


def _token(transaction_id: str) -> int:
    return int.from_bytes(UUID(transaction_id).bytes[:4], "big")


def _numeric_version(version: str) -> tuple[int, int, int]:
    parts = version.split("-", 1)[0].split("+", 1)[0].split(".")
    if len(parts) != 3 or any(not item.isascii() or not item.isdigit() for item in parts):
        raise OtaError("VERSION_UNREPRESENTABLE", "heartbeat requires a numeric version triple")
    result = tuple(int(item) for item in parts)
    if any(item > 255 for item in result):
        raise OtaError("VERSION_UNREPRESENTABLE", "heartbeat version components exceed uint8")
    return result


class ZonalAgent:
    def __init__(
        self, config: ZonalAgentConfig, installer: ABSlotInstaller, transport: CanTransport,
        *, fetch_bytes: Callable = fetch_https_bytes, artifact_downloader: Callable = download_https,
        now: Callable = lambda: datetime.now(timezone.utc), monotonic: Callable = time.monotonic,
        command_timeout_s: float = 30,
        application_adapter: ClusterApplicationAdapter | None = None,
        initial_software_version: tuple[int, int, int] = (0, 0, 0),
        protocol_version: tuple[int, int] = (1, 0),
    ):
        if not math.isfinite(command_timeout_s) or command_timeout_s <= 0:
            raise ValueError("command timeout must be finite and positive")
        self.config, self.installer, self.transport = config, installer, transport
        self.fetch_bytes, self.artifact_downloader = fetch_bytes, artifact_downloader
        self.now, self.monotonic, self.command_timeout_s = now, monotonic, command_timeout_s
        self.application_adapter = application_adapter
        self.initial_software_version, self.protocol_version = initial_software_version, protocol_version
        if type(protocol_version) is not tuple or len(protocol_version) != 2:
            raise ValueError("protocol_version must be a pair")
        HeartbeatFrame(config.ecu_id, initial_software_version, *protocol_version,
                       ApplicationState.STABLE, 0).encode()
        if config.install_root.resolve() != installer.install_root.resolve() or config.service_unit != installer.service_unit:
            raise OtaError("ZONAL_CONFIG_MISMATCH", "installer must own the configured root and service")
        if (config.state_file.resolve() == installer.state_path.resolve()
                or config.state_file.resolve().is_relative_to(installer.slots_dir.resolve())):
            raise OtaError("ZONAL_STATE_INVALID", "token journal must be separate from managed slots and slot journal")
        self.token_history = self._load_history()
        # Reconstruct missing mappings from durable slot UUIDs before recovery.
        state = installer.state
        retained_transactions = list(state.completed_transactions)
        if state.transaction_id:
            retained_transactions.append(state.transaction_id)
        for tx in retained_transactions:
            self._retain(tx)
        self._save_history()
        installer.recover_on_startup()
        self._previous_command: OtaCommandFrame | None = None
        self._previous_at: float | None = None
        self._status_counter = 0
        self._heartbeat_counter = 0
        self._vehicle_counter: int | None = None
        self.last_error: OtaError | None = None

    def _load_history(self) -> dict[str, str]:
        path = self.config.state_file
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(), object_pairs_hook=unique_fields)
            if (not isinstance(data, dict) or set(data) != {"schema_version", "token_history"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or not isinstance(data["token_history"], dict)):
                raise ValueError("invalid token journal")
            for key, tx in data["token_history"].items():
                _uuid(tx)
                if key != f"{_token(tx):08x}":
                    raise ValueError("token does not match UUID")
            return data["token_history"]
        except (OSError, ValueError, TypeError) as exc:
            raise OtaError("ZONAL_STATE_INVALID", "cannot load token history") from exc

    def _retain(self, tx: str) -> None:
        key = f"{_token(tx):08x}"
        if key in self.token_history and self.token_history[key] != tx:
            raise OtaError("TRANSACTION_TOKEN_COLLISION", "token belongs to retained UUID history")
        self.token_history[key] = tx

    def _save_history(self) -> None:
        path = self.config.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"schema_version": 1, "token_history": self.token_history}, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _root(self, token: int) -> str:
        return f"{self.config.central_base_url}/transactions/{token:08x}/cluster/"

    def _metadata(self, root: str, name: str, maximum: int) -> bytes:
        raw = self.fetch_bytes(root + name, self.config.ca_file, maximum)
        if not isinstance(raw, bytes) or len(raw) > maximum:
            raise OtaError("METADATA_TOO_LARGE", "metadata is invalid or exceeds configured limit")
        return raw

    def _binding(self, command: OtaCommandFrame) -> tuple[str, str]:
        try:
            data = json.loads(self._metadata(self._root(command.transaction_token), "transaction.json", 4096),
                              object_pairs_hook=unique_fields)
            if (not isinstance(data, dict) or set(data) != {"schema_version", "transaction_id", "release_job_id"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1):
                raise ValueError("invalid transaction binding")
            tx, job = _uuid(data["transaction_id"]), _uuid(data["release_job_id"])
            if _token(tx) != command.transaction_token:
                raise ValueError("transaction token mismatch")
            key = f"{command.transaction_token:08x}"
            if key in self.token_history and self.token_history[key] != tx:
                raise OtaError("TRANSACTION_TOKEN_COLLISION", "token belongs to retained UUID history")
            return tx, job
        except (ValueError, TypeError, UnicodeError) as exc:
            raise OtaError("TRANSACTION_INVALID", "invalid transaction metadata") from exc

    def _prepare(self, command: OtaCommandFrame) -> str:
        tx, job = self._binding(command)
        state = self.installer.state
        if tx in state.completed_transactions:
            return tx
        if state.transaction_id is not None:
            if state.transaction_id != tx:
                raise OtaError("SLOT_BUSY", "another transaction owns the trial slot")
            if command.slot.name != state.trial_slot:
                raise OtaError("SLOT_MISMATCH", "command selects a different trial slot")
            return tx
        if command.slot.name == state.stable_slot:
            raise OtaError("SLOT_MISMATCH", "PREPARE must select the inactive slot")
        root = self._root(command.transaction_token)
        manifest = ReleaseManifest.from_bytes(self._metadata(root, "manifest.json", 65536))
        signature = self._metadata(root, "manifest.sig", 64)
        verify_manifest_signature(manifest, signature, self.config.public_key)
        if str(UUID(manifest.job_id)) != job:
            raise OtaError("TRANSACTION_INVALID", "release job differs from transaction metadata")
        current_version = state.stable_version or ".".join(str(item) for item in self.initial_software_version)
        manifest.validate_for(self.config.device_id, current_version, self.now())
        _numeric_version(manifest.version)
        if manifest.artifact_size > self.config.download_max_bytes:
            raise OtaError("DOWNLOAD_TOO_LARGE", "declared archive exceeds configured limit")
        # Bind UUID durably before a crash can leave a staged slot behind.
        self._retain(tx)
        self._save_history()
        staging = self.config.install_root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"zonal-{command.transaction_token:08x}-", dir=staging) as name:
            work = Path(name)
            archive = work / "artifact.tar.gz"
            self.artifact_downloader(root + "artifact.tar.gz", archive, self.config.ca_file,
                                     manifest.artifact_size, self.config.download_max_bytes)
            digest = hashlib.sha256()
            total = 0
            with archive.open("rb") as stream:
                while chunk := stream.read(65536):
                    total += len(chunk)
                    if total > self.config.download_max_bytes or total > manifest.artifact_size:
                        raise OtaError("DOWNLOAD_SIZE_MISMATCH", "archive exceeds signed size")
                    digest.update(chunk)
            if total != manifest.artifact_size or digest.hexdigest() != manifest.artifact_sha256:
                raise OtaError("ARTIFACT_HASH_MISMATCH", "archive differs from signed release")
            extracted = work / "application"
            safe_extract_tar(archive, extracted, ArchiveLimits(self.config.archive_max_files, self.config.archive_max_bytes))
            self.installer.stage(tx, extracted, manifest.entrypoint, manifest.version)
        return tx

    def _transaction(self, command: OtaCommandFrame) -> str:
        tx = self.token_history.get(f"{command.transaction_token:08x}")
        if tx is None:
            raise OtaError("TRANSACTION_MISMATCH", "unknown transaction token")
        state = self.installer.state
        if state.transaction_id != tx and tx not in state.completed_transactions:
            raise OtaError("TRANSACTION_MISMATCH", "transaction has no staged or terminal outcome")
        if state.transaction_id == tx and command.slot.name != state.trial_slot:
            raise OtaError("SLOT_MISMATCH", "command selects a different trial slot")
        return tx

    def _status_for(self, tx: str | None) -> OtaStatus:
        state = self.installer.state
        outcome = state.completed_transactions.get(tx)
        if outcome:
            return OtaStatus.COMMITTED if outcome == "committed" else OtaStatus.ROLLED_BACK
        if state.transaction_id == tx and tx is not None:
            return {"staging": OtaStatus.PREPARING, "staged": OtaStatus.READY,
                    "activating": OtaStatus.ACTIVATING, "trial": OtaStatus.VERIFYING,
                    "committing": OtaStatus.VERIFYING, "rolling_back": OtaStatus.ROLLING_BACK}[state.phase]
        return OtaStatus.IDLE

    def _response(self, command: OtaCommandFrame, status: OtaStatus, detail: ZonalDetail = ZonalDetail.NONE) -> OtaStatusFrame:
        try:
            state = self.installer.state
            pending = state.transaction_id and _token(state.transaction_id) == command.transaction_token
            slot = Slot[state.trial_slot if pending else state.active_slot]
        except OtaError as exc:
            self.last_error = exc
            status, detail, slot = OtaStatus.RECOVERY_FAILED, ZonalDetail.STATE, command.slot
        result = OtaStatusFrame(status, command.transaction_token, slot, int(detail), self._status_counter)
        self._status_counter = (self._status_counter + 1) % 16
        return result

    def handle_command(self, frame: CanFrame) -> OtaStatusFrame:
        """Decode and execute one command. Bad CRC/envelope raises ValueError.

        First counter establishes a session; thereafter accept exact retries or
        one modulo-16 increment within command_timeout_s. QUERY_STATUS can
        establish a fresh counter session after the timeout. Failures return ERROR;
        last_error retains the structured local diagnostic.
        """
        command = OtaCommandFrame.decode(frame)
        self.last_error = None
        previous = self._previous_command
        if previous is not None:
            elapsed = self.monotonic() - self._previous_at
            resync = command.command == OtaCommand.QUERY_STATUS and elapsed > self.command_timeout_s
            if not resync and (elapsed < 0 or elapsed > self.command_timeout_s
                    or (command != previous and not is_counter_contiguous(command.counter, previous.counter, bits=4))):
                self.last_error = OtaError("COMMAND_STALE", "command counter or elapsed-time window is stale")
                return self._response(command, OtaStatus.ERROR, ZonalDetail.STALE)
        self._previous_command = command
        try:
            if command.flags:
                raise OtaError("COMMAND_INVALID", "v1 command flags must be zero")
            if command.command == OtaCommand.PREPARE:
                tx = self._prepare(command)
            elif command.command == OtaCommand.QUERY_STATUS:
                tx = self.token_history.get(f"{command.transaction_token:08x}")
                if tx is None and command.transaction_token != 0:
                    raise OtaError("TRANSACTION_MISMATCH", "unknown transaction token")
            else:
                tx = self._transaction(command)
                operation = {OtaCommand.ACTIVATE: self.installer.activate_trial,
                             OtaCommand.COMMIT: self.installer.commit,
                             OtaCommand.ROLLBACK: self.installer.rollback}[command.command]
                operation(tx)
            return self._response(command, self._status_for(tx))
        except (OtaError, OSError) as exc:
            self.last_error = exc if isinstance(exc, OtaError) else OtaError("ZONAL_IO_FAILED", str(exc))
            code = self.last_error.code
            detail = (ZonalDetail.REJECTED if code == "COMMAND_INVALID" else
                      ZonalDetail.TOKEN if code.startswith("TRANSACTION_") else
                      ZonalDetail.SLOT if code.startswith("SLOT_") else ZonalDetail.UPDATE)
            return self._response(command, OtaStatus.ERROR, detail)
        finally:
            self._previous_at = self.monotonic()

    def publish_heartbeat(self) -> HeartbeatFrame:
        state = self.installer.state
        trial = state.trial_slot == state.active_slot and state.trial_slot is not None
        version = state.trial_version if trial else state.stable_version
        heartbeat = HeartbeatFrame(self.config.ecu_id,
                                   _numeric_version(version) if version else self.initial_software_version,
                                   *self.protocol_version,
                                   ApplicationState.TRIAL if trial else ApplicationState.STABLE,
                                   self._heartbeat_counter)
        self.transport.send(heartbeat.encode())
        self._heartbeat_counter = (self._heartbeat_counter + 1) % 256
        return heartbeat

    def handle_functional_request(self, frame: CanFrame) -> FunctionalTestResultFrame:
        request = FunctionalTestRequestFrame.decode(frame)
        if self.application_adapter is None:
            raise OtaError("APPLICATION_ADAPTER_UNAVAILABLE", "application observation adapter is required")
        try:
            observed = self.application_adapter.observe_functional_test(request)
        except OSError as exc:
            raise OtaError("APPLICATION_OBSERVATION_FAILED", "cannot observe Cluster application") from exc
        if not isinstance(observed, ApplicationObservation):
            raise OtaError("APPLICATION_OBSERVATION_INVALID", "adapter must return displayed application values")
        result = FunctionalTestResultFrame(observed.speed, observed.rpm, observed.gear, observed.warnings, request.test_id)
        result.encode()  # Reject unrepresentable observations before publication.
        return result

    def poll_once(self, timeout: float) -> OtaStatusFrame | FunctionalTestResultFrame | None:
        """Process at most one CAN sample; ignore malformed/irrelevant frames."""
        try:
            frame = self.transport.recv(timeout)
            if frame is None:
                return None
            if frame.can_id == 0x600:
                status = self.handle_command(frame)
            elif frame.can_id == 0x610:
                status = self.handle_functional_request(frame)
            elif frame.can_id == 0x200:
                sample = VehicleStatusFrame.decode(frame)
                if self.application_adapter is not None and is_counter_contiguous(sample.counter, self._vehicle_counter):
                    self.application_adapter.apply_vehicle_status(sample)
                    self._vehicle_counter = sample.counter
                return None
            else:
                return None
        except ValueError:
            return None
        except OtaError as exc:
            self.last_error = exc
            return None
        self.transport.send(status.encode())
        return status
