from __future__ import annotations

import hashlib
import json
import shutil
import ssl
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import verify_manifest_signature

from .archive import ArchiveLimits, safe_extract_tar
from .config import AgentConfig
from .downloader import DownloadResult, download_https
from .installer import ReleaseInstaller
from .state import OtaState


_COMMAND_FIELDS = {
    "schema_version",
    "job_id",
    "device_id",
    "version",
    "manifest_url",
    "signature_url",
}


@dataclass(frozen=True)
class UpdateResult:
    job_id: str
    version: str
    stage: str
    success: bool
    rolled_back: bool = False
    error_code: str | None = None


def fetch_https_bytes(url: str, ca_file: Path, max_bytes: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise OtaError("DOWNLOAD_URL_INVALID", "metadata URL must be HTTPS")
    context = ssl.create_default_context(cafile=str(ca_file))
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "capstone-ota/1"})
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                raise OtaError("METADATA_TOO_LARGE", "metadata exceeds configured limit")
            content = response.read(max_bytes + 1)
    except OtaError:
        raise
    except (ValueError, urllib.error.URLError, ssl.SSLError, OSError) as exc:
        raise OtaError("DOWNLOAD_FAILED", "cannot download signed metadata") from exc
    if len(content) > max_bytes:
        raise OtaError("METADATA_TOO_LARGE", "metadata exceeds configured limit")
    return content


def _parse_command(payload: bytes) -> dict[str, object]:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise OtaError("COMMAND_INVALID", f"duplicate command key: {key}")
            result[key] = value
        return result

    try:
        command = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except OtaError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OtaError("COMMAND_INVALID", "command must be UTF-8 JSON") from exc
    if not isinstance(command, dict) or set(command) != _COMMAND_FIELDS:
        raise OtaError("COMMAND_INVALID", "command fields do not match schema version 1")
    if command.get("schema_version") != 1:
        raise OtaError("COMMAND_INVALID", "unsupported command schema")
    for field in _COMMAND_FIELDS - {"schema_version"}:
        if not isinstance(command[field], str) or not command[field]:
            raise OtaError("COMMAND_INVALID", f"{field} must be a non-empty string")
    try:
        uuid.UUID(command["job_id"])
    except ValueError as exc:
        raise OtaError("COMMAND_INVALID", "job_id must be a UUID") from exc
    for field in ("manifest_url", "signature_url"):
        parsed = urlparse(command[field])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise OtaError("COMMAND_INVALID", f"{field} must use HTTPS")
    return command


class UpdateAgent:
    def __init__(
        self,
        config: AgentConfig,
        installer: ReleaseInstaller,
        status_sink: Callable[[dict[str, object]], None],
        fetch_bytes: Callable = fetch_https_bytes,
        artifact_downloader: Callable = download_https,
        extractor: Callable = safe_extract_tar,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.config = config
        self.installer = installer
        self._status_sink = status_sink
        self._fetch_bytes = fetch_bytes
        self._artifact_downloader = artifact_downloader
        self._extractor = extractor
        self._now = now
        self._lock = threading.Lock()
        self._job_id = "unknown"
        self._version = "unknown"

    def publish_status(
        self, stage: str, progress: int, error: OtaError | None = None, **extra
    ) -> dict[str, object]:
        status: dict[str, object] = {
            "schema_version": 1,
            "device_id": self.config.device_id,
            "job_id": self._job_id,
            "version": self._version,
            "stage": stage,
            "progress": progress,
        }
        status.update(extra)
        if error is not None:
            status["error"] = {"code": error.code, "message": error.message}
        self._status_sink(status)
        return status

    @staticmethod
    def _result_from_status(status: dict[str, object]) -> UpdateResult:
        error = status.get("error")
        return UpdateResult(
            job_id=str(status["job_id"]),
            version=str(status["version"]),
            stage=str(status["stage"]),
            success=bool(status.get("success", status["stage"] == "success")),
            rolled_back=bool(status.get("rolled_back", status["stage"] == "rollback")),
            error_code=error.get("code") if isinstance(error, dict) else None,
        )

    def handle_command(self, payload: bytes) -> UpdateResult:
        if not self._lock.acquire(blocking=False):
            error = OtaError("BUSY", "another update is already running")
            status = self.publish_status("failed", 0, error, success=False)
            return self._result_from_status(status)
        archive_path: Path | None = None
        extracted_path: Path | None = None
        state = OtaState.load(self.config.state_file)
        if state.current_version is None:
            state.current_version = self.installer.active_version()
        reserved = False
        try:
            command = _parse_command(payload)
            self._job_id = str(command["job_id"])
            self._version = str(command["version"])
            completed = state.completed_jobs.get(self._job_id)
            if completed is not None:
                self._status_sink(completed)
                return self._result_from_status(completed)
            self.publish_status("received", 0)
            if command["device_id"] != self.config.device_id:
                raise OtaError("WRONG_DEVICE", "command targets a different device")
            self.publish_status("downloading", 10)
            manifest_bytes = self._fetch_bytes(command["manifest_url"], self.config.ca_file, 64 * 1024)
            signature = self._fetch_bytes(command["signature_url"], self.config.ca_file, 1024)
            manifest = ReleaseManifest.from_bytes(manifest_bytes)
            if (
                manifest.job_id != self._job_id
                or manifest.device_id != command["device_id"]
                or manifest.version != self._version
            ):
                raise OtaError("COMMAND_MISMATCH", "command does not match signed manifest")
            self.publish_status("verifying", 25)
            verify_manifest_signature(manifest, signature, self.config.public_key)
            manifest.validate_for(self.config.device_id, state.current_version, self._now())
            state.active_job = self._job_id
            state.save_atomic(self.config.state_file)
            reserved = True

            archive_path = self.installer.staging_dir / f"{self._job_id}.tar.gz"
            self.publish_status("downloading", 40)
            downloaded: DownloadResult = self._artifact_downloader(
                manifest.artifact_url,
                archive_path,
                self.config.ca_file,
                manifest.artifact_size,
                self.config.download_max_bytes,
            )
            if downloaded.size != manifest.artifact_size:
                raise OtaError("DOWNLOAD_SIZE_MISMATCH", "artifact size differs from manifest")
            if downloaded.sha256 != manifest.artifact_sha256:
                raise OtaError("ARTIFACT_HASH_MISMATCH", "artifact hash differs from manifest")

            self.publish_status("staging", 60)
            extracted_path = self.installer.staging_dir / self._job_id
            self._extractor(
                archive_path,
                extracted_path,
                ArchiveLimits(self.config.archive_max_files, self.config.archive_max_bytes),
            )
            self.installer.install_staged(manifest.version, extracted_path, manifest.entrypoint)
            extracted_path = None
            self.publish_status("activating", 80)
            self.publish_status("health_check", 90)
            activation = self.installer.activate(manifest.version)
            prior_version = state.current_version
            state.active_job = None
            state.current_version = activation.active_version
            state.previous_version = prior_version
            if activation.rolled_back:
                if manifest.version not in state.failed_versions:
                    state.failed_versions.append(manifest.version)
                terminal = self.publish_status(
                    "rollback", 100, success=False, rolled_back=True
                )
                result = self._result_from_status(terminal)
            else:
                terminal = self.publish_status(
                    "success", 100, success=True, rolled_back=False
                )
                result = self._result_from_status(terminal)
            state.completed_jobs[self._job_id] = terminal
            state.save_atomic(self.config.state_file)
            return result
        except OtaError as error:
            if self._version != "unknown" and self._version not in state.failed_versions:
                state.failed_versions.append(self._version)
            state.active_job = None
            terminal = self.publish_status(
                "failed", 100 if reserved else 0, error, success=False, rolled_back=False
            )
            if self._job_id != "unknown":
                state.completed_jobs[self._job_id] = terminal
            state.save_atomic(self.config.state_file)
            return self._result_from_status(terminal)
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)
            if extracted_path is not None and extracted_path.exists():
                shutil.rmtree(extracted_path)
            self._lock.release()


class AgentMessageRouter:
    def __init__(self, device_id: str, agent: UpdateAgent):
        self.topic = f"capstone/{device_id}/ota/command"
        self.agent = agent

    def handle(self, topic: str, payload: bytes) -> UpdateResult | None:
        if topic != self.topic:
            return None
        return self.agent.handle_command(payload)
