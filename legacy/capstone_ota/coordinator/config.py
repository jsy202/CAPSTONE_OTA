"""Deployment-pinned coordinator configuration v1; reject extensions/typos."""
from dataclasses import dataclass, fields
import json
from pathlib import Path
import re

from capstone_ota.agent.zonal_config import unique_fields
from capstone_ota.common.errors import OtaError


@dataclass(frozen=True)
class CoordinatorConfig:
    device_id: str
    ecu_id: str
    peer_device_id: str
    peer_ecu_id: str
    hardware_id: str
    broker_host: str
    broker_port: int
    ca_file: Path
    client_cert: Path
    client_key: Path
    public_key: Path
    cache_root: Path
    https_bind: str
    https_port: int
    https_cert: Path
    https_key: Path
    can_interface: str
    can_bitrate: int
    install_root: Path
    state_file: Path
    service_unit: str
    stable_bundle: Path
    application_socket: Path
    download_max_bytes: int
    archive_max_files: int
    archive_max_bytes: int

    @classmethod
    def from_json(cls, path: Path):
        try:
            data = json.loads(Path(path).read_text(), object_pairs_hook=unique_fields)
            if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)} | {"schema_version"}:
                raise ValueError("exact configuration fields required")
            for name, expected in dict(schema_version=1, device_id="central-pi-01", ecu_id="central-control",
                    peer_device_id="cluster-pi-02", peer_ecu_id="digital-cluster", can_interface="can0",
                    can_bitrate=500000, https_bind="10.10.0.1", https_port=8443,
                    service_unit="central-control.service").items():
                if type(data[name]) is not type(expected) or data[name] != expected:
                    raise ValueError(f"invalid pinned {name}")
            del data["schema_version"]
            for name in ("hardware_id", "broker_host"):
                if not isinstance(data[name], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", data[name]):
                    raise ValueError(f"invalid {name}")
            for name in ("broker_port", "download_max_bytes", "archive_max_files", "archive_max_bytes"):
                if type(data[name]) is not int or data[name] <= 0:
                    raise ValueError(f"invalid {name}")
            if data["broker_port"] > 65535:
                raise ValueError("invalid broker port")
            trust = ("ca_file", "client_cert", "client_key", "public_key", "https_cert", "https_key", "stable_bundle")
            for name in (*trust, "cache_root", "install_root", "state_file", "application_socket"):
                if not isinstance(data[name], str) or "\0" in data[name] or not Path(data[name]).is_absolute():
                    raise ValueError(f"invalid path {name}")
                data[name] = Path(data[name])
                if name in trust and not data[name].is_file():
                    raise ValueError(f"missing trust/provisioning file {name}")
            root, state, cache = (data[key].resolve() for key in ("install_root", "state_file", "cache_root"))
            if state.is_relative_to(root) or state.is_relative_to(cache) or root.is_relative_to(cache) or cache.is_relative_to(root):
                raise ValueError("cache, slot root and transaction journal must be separate")
            return cls(**data)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise OtaError("CONFIG_INVALID", str(exc)) from exc
