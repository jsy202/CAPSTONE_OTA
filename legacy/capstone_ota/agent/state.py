from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from capstone_ota.common.errors import OtaError


_STATE_FIELDS = {
    "schema_version",
    "current_version",
    "previous_version",
    "completed_jobs",
    "failed_versions",
    "active_job",
}


@dataclass
class OtaState:
    current_version: str | None = None
    previous_version: str | None = None
    completed_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed_versions: list[str] = field(default_factory=list)
    active_job: str | None = None
    schema_version: int = field(default=1, init=False)

    @classmethod
    def load(cls, path: Path) -> "OtaState":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OtaError("STATE_INVALID", "OTA state is unreadable") from exc
        if not isinstance(data, dict) or set(data) != _STATE_FIELDS or data.get("schema_version") != 1:
            raise OtaError("STATE_INVALID", "OTA state schema is invalid")
        for name in ("current_version", "previous_version", "active_job"):
            if data[name] is not None and not isinstance(data[name], str):
                raise OtaError("STATE_INVALID", f"{name} must be a string or null")
        completed = data["completed_jobs"]
        failed = data["failed_versions"]
        if (
            not isinstance(completed, dict)
            or not all(isinstance(key, str) and isinstance(value, dict) for key, value in completed.items())
            or not isinstance(failed, list)
            or not all(isinstance(item, str) for item in failed)
        ):
            raise OtaError("STATE_INVALID", "OTA job history is invalid")
        return cls(
            current_version=data["current_version"],
            previous_version=data["previous_version"],
            completed_jobs=dict(list(completed.items())[-100:]),
            failed_versions=failed,
            active_job=data["active_job"],
        )

    def save_atomic(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.completed_jobs = dict(list(self.completed_jobs.items())[-100:])
        content = json.dumps(asdict(self), separators=(",", ":")).encode("utf-8")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
