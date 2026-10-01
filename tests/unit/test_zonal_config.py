import importlib
import json

import pytest

from capstone_ota.common.errors import OtaError


def configuration(tmp_path):
    for name in ("ca.pem", "public.pem"):
        (tmp_path / name).write_text("trusted fixture")
    return {
        "schema_version": 1, "device_id": "cluster-pi-01", "ecu_id": "digital-cluster",
        "can_interface": "can0", "can_bitrate": 500000,
        "central_base_url": "https://10.10.0.1:8443",
        "ca_file": str(tmp_path / "ca.pem"), "public_key": str(tmp_path / "public.pem"),
        "install_root": str(tmp_path / "install"), "state_file": str(tmp_path / "zonal.json"),
        "service_unit": "digital-dash.service", "download_max_bytes": 100000,
        "archive_max_files": 100, "archive_max_bytes": 100000,
    }


def load(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return importlib.import_module("capstone_ota.agent.zonal_config").ZonalAgentConfig.from_json(path)


def test_load_pins_cluster_can_and_central_contract(tmp_path):
    config = load(tmp_path, configuration(tmp_path))
    assert config.device_id == "cluster-pi-01"
    assert config.ecu_id == "digital-cluster"
    assert config.can_interface == "can0"
    assert config.can_bitrate == 500000
    assert config.central_base_url == "https://10.10.0.1:8443"
    assert config.install_root == tmp_path / "install"
    assert config.state_file == tmp_path / "zonal.json"
    assert config.service_unit == "digital-dash.service"


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("unknown", 1),
    ("device_id", "bad/name"), ("ecu_id", "central-control"),
    ("can_interface", "vcan0"), ("can_bitrate", 250000), ("can_bitrate", True),
    ("central_base_url", "http://10.10.0.1:8443"),
    ("central_base_url", "https://elsewhere:8443"),
    ("ca_file", "relative.pem"), ("public_key", "/missing/public.pem"),
    ("install_root", "relative"), ("state_file", "relative"),
    ("service_unit", "bad;service"), ("service_unit", ""),
    ("download_max_bytes", 0), ("archive_max_files", True), ("archive_max_bytes", -1),
])
def test_rejects_invalid_or_unknown_configuration(tmp_path, field, value):
    data = configuration(tmp_path)
    data[field] = value
    with pytest.raises(OtaError, match="CONFIG_INVALID"):
        load(tmp_path, data)


def test_rejects_duplicate_and_missing_fields(tmp_path):
    module = importlib.import_module("capstone_ota.agent.zonal_config")
    path = tmp_path / "config.json"
    data = configuration(tmp_path)
    path.write_text(json.dumps(data)[:-1] + ',"can_bitrate":500000}')
    with pytest.raises(OtaError):
        module.ZonalAgentConfig.from_json(path)
    del data["state_file"]
    with pytest.raises(OtaError):
        load(tmp_path, data)
