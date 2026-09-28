from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from capstone_ota.common.errors import OtaError


_FIELDS = {
    "schema_version",
    "device_id",
    "broker_host",
    "broker_port",
    "ca_file",
    "client_cert",
    "client_key",
    "public_key",
    "install_root",
    "state_file",
    "download_max_bytes",
    "archive_max_files",
    "archive_max_bytes",
}
_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _invalid(message: str) -> OtaError:
    return OtaError("CONFIG_INVALID", message)


@dataclass(frozen=True)
class AgentConfig:
    device_id: str
    broker_host: str
    broker_port: int
    ca_file: Path
    client_cert: Path
    client_key: Path
    public_key: Path
    install_root: Path
    state_file: Path
    download_max_bytes: int
    archive_max_files: int
    archive_max_bytes: int

    @classmethod
    def from_json(cls, path: Path) -> "AgentConfig":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid("cannot read agent configuration") from exc
        if not isinstance(data, dict) or set(data) != _FIELDS or data.get("schema_version") != 1:
            raise _invalid("configuration fields do not match schema version 1")
        if not isinstance(data["device_id"], str) or not _DEVICE_RE.fullmatch(data["device_id"]):
            raise _invalid("invalid device_id")
        if not isinstance(data["broker_host"], str) or not data["broker_host"]:
            raise _invalid("broker_host is required")
        if type(data["broker_port"]) is not int or data["broker_port"] != 8883:
            raise _invalid("broker_port must be the TLS listener 8883")
        values = {}
        for field in ("ca_file", "client_cert", "client_key", "public_key", "install_root", "state_file"):
            if not isinstance(data[field], str):
                raise _invalid(f"{field} must be an absolute path")
            value = Path(data[field])
            if not value.is_absolute():
                raise _invalid(f"{field} must be an absolute path")
            values[field] = value
        for field in ("ca_file", "client_cert", "client_key", "public_key"):
            if not values[field].is_file():
                raise _invalid(f"trusted file does not exist: {field}")
        for field in ("download_max_bytes", "archive_max_files", "archive_max_bytes"):
            if type(data[field]) is not int or data[field] <= 0:
                raise _invalid(f"{field} must be a positive integer")
        return cls(
            device_id=data["device_id"],
            broker_host=data["broker_host"],
            broker_port=data["broker_port"],
            ca_file=values["ca_file"],
            client_cert=values["client_cert"],
            client_key=values["client_key"],
            public_key=values["public_key"],
            install_root=values["install_root"],
            state_file=values["state_file"],
            download_max_bytes=data["download_max_bytes"],
            archive_max_files=data["archive_max_files"],
            archive_max_bytes=data["archive_max_bytes"],
        )
