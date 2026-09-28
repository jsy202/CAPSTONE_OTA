import json
from pathlib import Path

import pytest

from capstone_ota.common.signing import generate_key_pair
from capstone_ota.publisher.cli import main


def make_payload(root):
    binary = root / "bin" / "digital-dash"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)


def test_package_prints_bundle_paths_without_private_key(tmp_path, capsys):
    payload = tmp_path / "payload"
    output = tmp_path / "releases"
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    make_payload(payload)
    generate_key_pair(private, public)

    result = main(
        [
            "package",
            "--payload", str(payload),
            "--output", str(output),
            "--device-id", "cluster-pi-01",
            "--version", "1.0.0",
            "--entrypoint", "bin/digital-dash",
            "--base-url", "https://laptop.local:8443/releases",
            "--private-key", str(private),
            "--expires-in", "3600",
        ]
    )

    printed = json.loads(capsys.readouterr().out)
    assert result == 0
    assert set(printed) == {"archive", "manifest", "signature"}
    assert all(Path(value).exists() for value in printed.values())
    assert str(private) not in json.dumps(printed)


def test_keys_requires_both_output_paths():
    with pytest.raises(SystemExit):
        main(["keys", "--private-key", "private.pem"])


def test_serve_requires_root_certificate_and_key(tmp_path):
    with pytest.raises(SystemExit):
        main(["serve", "--root", str(tmp_path), "--cert", "server.pem"])


def test_publish_rejects_non_https_base_url_before_mqtt_connection(tmp_path):
    manifest = tmp_path / "1.0.0.manifest.json"
    signature = tmp_path / "1.0.0.manifest.sig"
    manifest.write_text("{}")
    signature.write_bytes(b"signature")
    called = False

    def mqtt_factory(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not connect")

    with pytest.raises(SystemExit):
        main(
            [
                "publish",
                "--broker", "broker.local",
                "--ca", "ca.pem",
                "--cert", "publisher.pem",
                "--key", "publisher.key",
                "--device-id", "cluster-pi-01",
                "--manifest", str(manifest),
                "--signature", str(signature),
                "--base-url", "http://laptop/releases",
            ],
            mqtt_factory=mqtt_factory,
        )
    assert called is False
