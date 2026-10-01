"""Strict, immutable signed vehicle bundle schema v1.

Targets reference independently signed ReleaseManifest documents; this bundle
does not replace their signatures. Dependency bounds are inclusive SemVer
bounds (null means unbounded), ignoring build metadata. Health timeouts use
integer seconds; thresholds are integer counts. Arrays retain signed order.
Only central-control and digital-cluster are permitted, with rollback_scope all.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .errors import OtaError
from .manifest import _semver_key


_ROLES = {"central-control", "digital-cluster"}
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_HASH = re.compile(r"[0-9a-f]{64}")


def _invalid(message: str) -> OtaError:
    return OtaError("INVALID_VEHICLE_BUNDLE", message)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _schema(data: Any, cls: type) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != {field.name for field in fields(cls)}:
        raise _invalid(f"{cls.__name__} fields do not match schema v1")
    return data


def _name(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise _invalid(f"{label} must be an identifier of at most 64 characters")


def _integer(value: Any, label: str, minimum: int = 0, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise _invalid(f"{label} is outside its integer range")


def _version(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, str) or not value.isascii():
        raise _invalid("version must be ASCII SemVer")
    try:
        return _semver_key(value)
    except OtaError as exc:
        raise _invalid("version must be SemVer") from exc


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise _invalid("timestamps must be RFC 3339 UTC with Z suffix")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _invalid("invalid timestamp") from exc


def _url(value: Any) -> None:
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        raise _invalid("URLs must be HTTPS without whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or (port is not None and port == 0)):
            raise _invalid("URLs must be HTTPS without credentials")
    except ValueError as exc:
        raise _invalid("invalid HTTPS URL") from exc


def _capabilities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _invalid("capabilities must be an array")
    for capability in value:
        _name(capability, "capability")
    if len(set(value)) != len(value):
        raise _invalid("capabilities must be unique")
    return tuple(value)


@dataclass(frozen=True)
class EcuTarget:
    ecu_id: str
    hardware_id: str
    software_version: str
    entrypoint: str
    can_interface: str
    protocol_major: int
    protocol_minor: int
    capabilities: tuple[str, ...]
    release_manifest_url: str
    release_signature_url: str
    artifact_url: str
    artifact_size: int
    artifact_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))


@dataclass(frozen=True)
class DependencyRule:
    """The dependent requires the provider's declared hardware/CAN/capabilities."""
    dependent_ecu_id: str
    provider_ecu_id: str
    hardware_id: str
    min_version: str | None
    max_version: str | None
    can_interface: str
    protocol_major: int
    min_protocol_minor: int
    required_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))


@dataclass(frozen=True)
class HealthPolicy:
    prepare_timeout_s: int
    activation_timeout_s: int
    verification_timeout_s: int
    heartbeat_timeout_s: int
    max_missed_heartbeats: int
    max_error_count: int


@dataclass(frozen=True)
class VehicleBundleManifest:
    schema_version: int
    transaction_id: str
    bundle_version: str
    created_at: str
    expires_at: str
    targets: tuple[EcuTarget, ...]
    dependencies: tuple[DependencyRule, ...]
    health_policy: HealthPolicy
    rollback_scope: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))

    @classmethod
    def from_bytes(cls, raw: bytes) -> VehicleBundleManifest:
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid("bundle must be valid UTF-8 JSON") from exc
        _schema(data, cls)
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise _invalid("unsupported schema_version")
        if not isinstance(data["transaction_id"], str):
            raise _invalid("transaction_id must be a UUID")
        try:
            if str(uuid.UUID(data["transaction_id"])) != data["transaction_id"]:
                raise ValueError("noncanonical UUID")
        except ValueError as exc:
            raise _invalid("transaction_id must be a canonical UUID") from exc
        _version(data["bundle_version"])
        if _timestamp(data["expires_at"]) <= _timestamp(data["created_at"]):
            raise _invalid("expires_at must be after created_at")
        if data["rollback_scope"] != "all":
            raise _invalid("rollback_scope must be all")
        if not isinstance(data["targets"], list) or len(data["targets"]) != 2:
            raise _invalid("exactly two ECU targets are required")
        targets = []
        for target in data["targets"]:
            _schema(target, EcuTarget)
            for name in ("ecu_id", "hardware_id", "can_interface"):
                _name(target[name], name)
            _version(target["software_version"])
            entrypoint = target["entrypoint"]
            if (not isinstance(entrypoint, str) or len(entrypoint.split("/")) < 2
                    or any(part in {"", ".", ".."} for part in entrypoint.split("/"))
                    or "\\" in entrypoint or "\x00" in entrypoint):
                raise _invalid("entrypoint must be a normalized relative path")
            for name in ("protocol_major", "protocol_minor"):
                _integer(target[name], name, maximum=255)
            capabilities = _capabilities(target["capabilities"])
            for name in ("release_manifest_url", "release_signature_url", "artifact_url"):
                _url(target[name])
            _integer(target["artifact_size"], "artifact_size", minimum=1)
            if not isinstance(target["artifact_sha256"], str) or not _HASH.fullmatch(target["artifact_sha256"]):
                raise _invalid("artifact_sha256 must be lowercase hexadecimal SHA-256")
            targets.append(EcuTarget(**dict(target, capabilities=capabilities)))
        if {target.ecu_id for target in targets} != _ROLES:
            raise _invalid("targets must contain central-control and digital-cluster exactly once")
        if not isinstance(data["dependencies"], list):
            raise _invalid("dependencies must be an array")
        dependencies = []
        for rule in data["dependencies"]:
            _schema(rule, DependencyRule)
            for name in ("dependent_ecu_id", "provider_ecu_id", "hardware_id", "can_interface"):
                _name(rule[name], name)
            for name in ("min_version", "max_version"):
                if rule[name] is not None:
                    _version(rule[name])
            if (rule["min_version"] is not None and rule["max_version"] is not None
                    and _version(rule["min_version"]) > _version(rule["max_version"])):
                raise _invalid("dependency version bounds are reversed")
            _integer(rule["protocol_major"], "protocol_major", maximum=255)
            _integer(rule["min_protocol_minor"], "min_protocol_minor", maximum=255)
            capabilities = _capabilities(rule["required_capabilities"])
            dependencies.append(DependencyRule(**dict(rule, required_capabilities=capabilities)))
        policy = _schema(data["health_policy"], HealthPolicy)
        for name, value in policy.items():
            _integer(value, name, minimum=0 if name.startswith("max_") else 1)
        return cls(**dict(data, targets=tuple(targets), dependencies=tuple(dependencies),
                          health_policy=HealthPolicy(**policy)))

    @property
    def transaction_token(self) -> int:
        """First four UUID bytes, in network (big-endian) order."""
        return int.from_bytes(uuid.UUID(self.transaction_id).bytes[:4], "big")

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")

    def validate_at(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise _invalid("now must be timezone-aware")
        now = now.astimezone(timezone.utc)
        if now < _timestamp(self.created_at):
            raise OtaError("BUNDLE_NOT_YET_VALID", "bundle creation time is in the future")
        if now > _timestamp(self.expires_at):
            raise OtaError("BUNDLE_EXPIRED", "bundle has expired")

    def validate_dependencies(self) -> None:
        """Check each signed consumer requirement against its provider.

        Both endpoints must declare the rule's interface and exact major.
        The provider's minor must meet the consumer's explicit rule minimum;
        it may be higher, establishing the declared backward compatibility.
        Hardware, inclusive version bounds, and capabilities apply to provider.
        """
        targets = {target.ecu_id: target for target in self.targets}
        for rule in self.dependencies:
            dependent = targets.get(rule.dependent_ecu_id)
            provider = targets.get(rule.provider_ecu_id)
            if dependent is None or provider is None:
                raise OtaError("DEPENDENCY_UNSATISFIED", "required dependency ECU is absent")
            version = _version(provider.software_version)
            if (
                provider.hardware_id != rule.hardware_id
                or (rule.min_version is not None and version < _version(rule.min_version))
                or (rule.max_version is not None and version > _version(rule.max_version))
                or provider.can_interface != rule.can_interface
                or dependent.can_interface != rule.can_interface
                or provider.protocol_major != rule.protocol_major
                or dependent.protocol_major != rule.protocol_major
                or provider.protocol_minor < rule.min_protocol_minor
                or not set(rule.required_capabilities).issubset(provider.capabilities)
            ):
                raise OtaError("DEPENDENCY_UNSATISFIED",
                               f"{rule.provider_ecu_id} does not satisfy {rule.dependent_ecu_id}")
