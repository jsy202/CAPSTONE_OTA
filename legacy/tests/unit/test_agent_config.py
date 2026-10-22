import json

import pytest

from capstone_ota.agent.config import AgentConfig
from capstone_ota.common.errors import OtaError


def config_document(tmp_path, **overrides):
    trust = tmp_path / "trust"
    trust.mkdir(exist_ok=True)
    for name in ("ca.pem", "client.pem", "client.key", "update-public.pem"):
        (trust / name).write_text("fixture")
    data = {
        "schema_version": 1,
        "device_id": "cluster-pi-01",
        "broker_host": "laptop.local",
        "broker_port": 8883,
        "ca_file": str(trust / "ca.pem"),
        "client_cert": str(trust / "client.pem"),
        "client_key": str(trust / "client.key"),
        "public_key": str(trust / "update-public.pem"),
        "install_root": str(tmp_path / "opt" / "capstone"),
        "state_file": str(tmp_path / "var" / "state.json"),
        "download_max_bytes": 100_000_000,
        "archive_max_files": 10_000,
        "archive_max_bytes": 250_000_000,
    }
    data.update(overrides)
    return data


def write_config(tmp_path, data):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(data))
    return path


def test_agent_config_loads_required_immutable_values(tmp_path):
    config = AgentConfig.from_json(write_config(tmp_path, config_document(tmp_path)))

    assert config.device_id == "cluster-pi-01"
    assert config.broker_port == 8883
    assert config.install_root.is_absolute()
    with pytest.raises(AttributeError):
        config.device_id = "changed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop("public_key"),
        lambda data: data.update({"unexpected": True}),
        lambda data: data.update({"install_root": "relative/root"}),
        lambda data: data.update({"broker_port": 1883}),
        lambda data: data.update({"download_max_bytes": 0}),
        lambda data: data.update({"device_id": "../other"}),
    ],
)
def test_agent_config_rejects_missing_unknown_relative_or_insecure_values(tmp_path, mutation):
    data = config_document(tmp_path)
    mutation(data)
    with pytest.raises(OtaError) as error:
        AgentConfig.from_json(write_config(tmp_path, data))
    assert error.value.code == "CONFIG_INVALID"


def test_agent_config_rejects_missing_trust_file(tmp_path):
    data = config_document(tmp_path)
    data["public_key"] = str(tmp_path / "missing.pem")
    with pytest.raises(OtaError) as error:
        AgentConfig.from_json(write_config(tmp_path, data))
    assert error.value.code == "CONFIG_INVALID"
