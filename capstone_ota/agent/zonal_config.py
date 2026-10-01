"""Strict Cluster configuration v1; SocketCAN bitrate setup is external."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from pathlib import Path

from capstone_ota.common.errors import OtaError


def unique_fields(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


@dataclass(frozen=True)
class ZonalAgentConfig:
    device_id: str
    ecu_id: str
    can_interface: str
    can_bitrate: int
    central_base_url: str
    ca_file: Path
    public_key: Path
    install_root: Path
    state_file: Path
    service_unit: str
    download_max_bytes: int
    archive_max_files: int
    archive_max_bytes: int

    @classmethod
    def from_json(cls, path: Path) -> ZonalAgentConfig:
        invalid = OtaError("CONFIG_INVALID", "invalid Cluster configuration v1")
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
            if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)} | {"schema_version"}:
                raise invalid
            schema_version = data.pop("schema_version")
            if type(schema_version) is not int or schema_version != 1:
                raise invalid
            if data["device_id"] != "cluster-pi-02":
                raise invalid
            if (data["ecu_id"] != "digital-cluster" or data["can_interface"] != "can0"
                    or type(data["can_bitrate"]) is not int or data["can_bitrate"] != 500000
                    or data["central_base_url"] != "https://10.10.0.1:8443"):
                raise invalid
            if not isinstance(data["service_unit"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.service", data["service_unit"]):
                raise invalid
            for name in ("ca_file", "public_key", "install_root", "state_file"):
                if not isinstance(data[name], str) or "\0" in data[name] or not Path(data[name]).is_absolute():
                    raise invalid
                data[name] = Path(data[name])
            for name in ("ca_file", "public_key"):
                if not data[name].is_file():
                    raise invalid
            for name in ("download_max_bytes", "archive_max_files", "archive_max_bytes"):
                if type(data[name]) is not int or data[name] <= 0:
                    raise invalid
            return cls(**data)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise invalid from exc
