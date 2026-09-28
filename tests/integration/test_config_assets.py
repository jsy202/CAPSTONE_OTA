from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
OTA = ROOT / "ota"


def _directives(path: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        result.setdefault(key, []).append(value.strip())
    return result


def test_mosquitto_requires_mutual_tls_and_acl():
    directives = _directives(OTA / "broker/mosquitto.conf")
    assert directives["listener"] == ["8883 0.0.0.0"]
    assert directives["allow_anonymous"] == ["false"]
    assert directives["require_certificate"] == ["true"]
    assert directives["use_identity_as_username"] == ["true"]
    assert directives["tls_version"] == ["tlsv1.2"]
    assert directives["acl_file"] == ["/etc/mosquitto/acl.capstone"]
    for name in ("cafile", "certfile", "keyfile"):
        assert directives[name][0].startswith("/etc/capstone-ota/pki/")

    acl = (OTA / "broker/acl.template").read_text(encoding="utf-8")
    assert "user publisher" in acl
    assert "topic write capstone/+/ota/command" in acl
    assert "topic read capstone/+/ota/status" in acl
    assert "pattern read capstone/%u/ota/command" in acl
    assert "pattern write capstone/%u/ota/status" in acl


def test_sample_configs_are_parseable_and_contain_no_secrets():
    laptop = json.loads((OTA / "config/laptop.example.json").read_text())
    pi = json.loads((OTA / "config/pi.example.json").read_text())
    assert laptop["schema_version"] == pi["schema_version"] == 1
    assert laptop["broker_port"] == pi["broker_port"] == 8883
    assert pi["device_id"] == "cluster-pi-01"
    assert all(Path(pi[key]).is_absolute() for key in (
        "ca_file", "client_cert", "client_key", "public_key", "install_root", "state_file"
    ))
    serialized = json.dumps([laptop, pi]).lower()
    assert "password" not in serialized
    assert "private_key" not in serialized

    committed_secret_suffixes = {".key", ".pem", ".p12", ".pfx"}
    assert not [p for p in OTA.rglob("*") if p.is_file() and p.suffix in committed_secret_suffixes]


def test_systemd_units_use_fixed_paths_and_least_privilege():
    agent = (OTA / "systemd/capstone-ota-agent.service").read_text()
    dash = (OTA / "systemd/digital-dash.service").read_text()
    assert "User=capstone-ota" in agent
    assert "ExecStart=/opt/capstone-ota/venv/bin/capstone-ota-agent --config /etc/capstone-ota/agent.json" in agent
    assert "ReadWritePaths=/var/lib/capstone-ota /opt/digital-dash" in agent
    assert "ProtectSystem=strict" in agent
    assert "User=digital-dash" in dash
    assert "ExecStart=/opt/digital-dash/current/bin/digital-dash" in dash
    assert "ProtectSystem=strict" in dash
    assert "ReadWritePaths=/opt/digital-dash" not in dash


def test_install_scripts_create_separate_service_accounts():
    laptop = (OTA / "scripts/install-laptop.sh").read_text()
    pi = (OTA / "scripts/install-pi.sh").read_text()
    assert "capstone-ota" in laptop
    assert "useradd" in pi and "capstone-ota" in pi and "digital-dash" in pi
    assert "systemctl enable" not in laptop
    assert "systemctl enable" not in pi


def test_pki_generator_creates_expected_keys_and_refuses_overwrite(tmp_path):
    script = OTA / "scripts/generate-dev-pki.sh"
    output = tmp_path / "pki"
    subprocess.run(["bash", str(script), "localhost", "cluster-pi-01", str(output)], check=True)
    expected = {
        "ca.crt", "ca.key", "https.crt", "https.key", "mqtt.crt", "mqtt.key",
        "publisher.crt", "publisher.key", "cluster-pi-01.crt", "cluster-pi-01.key",
        "update-signing.key", "update-signing.pub",
    }
    assert expected <= {path.name for path in output.iterdir()}
    for name in expected:
        assert os.stat(output / name).st_mode & 0o077 == 0
    cert = subprocess.run(
        ["openssl", "x509", "-in", str(output / "https.crt"), "-noout", "-ext", "subjectAltName"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "DNS:localhost" in cert
    repeated = subprocess.run(
        ["bash", str(script), "localhost", "cluster-pi-01", str(output)], capture_output=True, text=True
    )
    assert repeated.returncode != 0
    assert "refus" in repeated.stderr.lower() or "exists" in repeated.stderr.lower()
