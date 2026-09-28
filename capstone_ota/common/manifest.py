from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .errors import OtaError


_FIELDS = {
    "schema_version",
    "job_id",
    "device_id",
    "version",
    "created_at",
    "expires_at",
    "artifact_url",
    "artifact_size",
    "artifact_sha256",
    "entrypoint",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _invalid(message: str) -> OtaError:
    return OtaError("INVALID_MANIFEST", message)


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_utc(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise _invalid(f"{field} must be RFC 3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _invalid(f"{field} must be RFC 3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise _invalid(f"{field} must be UTC")
    return parsed


def _semver_key(value: str) -> tuple[Any, ...]:
    match = _SEMVER_RE.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise _invalid("version must be SemVer")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    prerelease = match.group(4)
    if prerelease is None:
        pre_key: tuple[Any, ...] = (1,)
    else:
        identifiers: list[tuple[int, Any]] = []
        for item in prerelease.split("."):
            if item.isdigit():
                if len(item) > 1 and item.startswith("0"):
                    raise _invalid("numeric prerelease identifiers cannot have leading zeroes")
                identifiers.append((0, int(item)))
            else:
                identifiers.append((1, item))
        pre_key = (0, *identifiers)
    return major, minor, patch, pre_key


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: int
    job_id: str
    device_id: str
    version: str
    created_at: str
    expires_at: str
    artifact_url: str
    artifact_size: int
    artifact_sha256: str
    entrypoint: str

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ReleaseManifest":
        try:
            decoded = raw.decode("utf-8")
            data = json.loads(decoded, object_pairs_hook=_pairs_without_duplicates)
        except OtaError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid("manifest must be valid UTF-8 JSON") from exc

        if not isinstance(data, dict) or set(data) != _FIELDS:
            raise _invalid("manifest fields do not match schema version 1")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise _invalid("unsupported schema_version")
        for field in ("job_id", "device_id", "version", "created_at", "expires_at", "artifact_url", "artifact_sha256", "entrypoint"):
            if not isinstance(data[field], str) or not data[field]:
                raise _invalid(f"{field} must be a non-empty string")
        if type(data["artifact_size"]) is not int or data["artifact_size"] <= 0:
            raise _invalid("artifact_size must be a positive integer")
        try:
            uuid.UUID(data["job_id"])
        except ValueError as exc:
            raise _invalid("job_id must be a UUID") from exc
        if not _DEVICE_RE.fullmatch(data["device_id"]):
            raise _invalid("device_id has invalid characters")
        _semver_key(data["version"])
        created = _parse_utc(data["created_at"], "created_at")
        expires = _parse_utc(data["expires_at"], "expires_at")
        if expires <= created:
            raise _invalid("expires_at must be after created_at")
        url = urlparse(data["artifact_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise _invalid("artifact_url must be an HTTPS URL without credentials")
        if not _SHA256_RE.fullmatch(data["artifact_sha256"]):
            raise _invalid("artifact_sha256 must be lowercase hexadecimal")
        entrypoint = PurePosixPath(data["entrypoint"])
        if entrypoint.is_absolute() or len(entrypoint.parts) < 2 or any(
            part in {"", ".", ".."} for part in entrypoint.parts
        ):
            raise _invalid("entrypoint must be a normalized relative path")
        return cls(**data)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def validate_for(
        self, device_id: str, current_version: str | None, now: datetime
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise _invalid("now must be timezone-aware")
        if self.device_id != device_id:
            raise OtaError("WRONG_DEVICE", "manifest targets a different device")
        created = _parse_utc(self.created_at, "created_at")
        expires = _parse_utc(self.expires_at, "expires_at")
        current_time = now.astimezone(timezone.utc)
        if current_time < created:
            raise OtaError("NOT_YET_VALID", "manifest creation time is in the future")
        if current_time > expires:
            raise OtaError("EXPIRED", "manifest has expired")
        if current_version is not None and _semver_key(self.version) <= _semver_key(current_version):
            raise OtaError(
                "ROLLBACK_REJECTED",
                f"version {self.version} is not newer than {current_version}",
            )
