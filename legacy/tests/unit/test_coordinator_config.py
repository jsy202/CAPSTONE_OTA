import importlib
import json

import pytest

from capstone_ota.common.errors import OtaError


def config_data(tmp_path):
    data = dict(schema_version=1, device_id="central-pi-01", ecu_id="central-control",
                peer_device_id="cluster-pi-02", peer_ecu_id="digital-cluster", hardware_id="rpi-4b",
                broker_host="laptop.local", broker_port=8883, https_bind="10.10.0.1", https_port=8443,
                can_interface="can0", can_bitrate=500000, service_unit="central-control.service",
                download_max_bytes=104857600, archive_max_files=1000, archive_max_bytes=268435456)
    for name in ("ca_file", "client_cert", "client_key", "public_key", "https_cert", "https_key", "stable_bundle"):
        path = tmp_path / name
        path.write_text("provisioned")
        data[name] = str(path)
    for name in ("cache_root", "install_root", "state_file", "application_socket"):
        data[name] = str(tmp_path / name)
    return data


def load(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return importlib.import_module("capstone_ota.coordinator.config").CoordinatorConfig.from_json(path)


def test_valid_config_pins_central_private_network_and_peer(tmp_path):
    config = load(tmp_path, config_data(tmp_path))
    assert config.device_id == "central-pi-01"
    assert config.application_socket == tmp_path / "application_socket"


@pytest.mark.parametrize("field,value", [("device_id", "central-pi-02"), ("peer_device_id", "cluster-pi-01"),
    ("ecu_id", "digital-cluster"), ("peer_ecu_id", "central-control"), ("can_interface", "vcan0"),
    ("can_bitrate", 250000), ("https_bind", "0.0.0.0"), ("https_port", 443), ("broker_port", True),
    ("service_unit", "ota-coordinator.service"), ("public_key", "/no/such/key"), ("cache_root", "relative"),
    ("download_max_bytes", False), ("broker_host", ""), ("unexpected", 1)])
def test_invalid_config_fails_closed(tmp_path, field, value):
    data = config_data(tmp_path)
    data[field] = value
    with pytest.raises(OtaError) as exc:
        load(tmp_path, data)
    assert exc.value.code == "CONFIG_INVALID"


@pytest.mark.parametrize("field", ["ca_file", "client_cert", "client_key", "public_key", "cache_root", "https_cert",
    "https_key", "install_root", "state_file", "stable_bundle", "application_socket", "peer_ecu_id"])
def test_required_config_fields_cannot_be_omitted(tmp_path, field):
    data = config_data(tmp_path)
    del data[field]
    with pytest.raises(OtaError):
        load(tmp_path, data)
