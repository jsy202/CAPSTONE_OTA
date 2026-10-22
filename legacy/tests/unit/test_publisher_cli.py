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


def test_vehicle_package_signs_bound_releases_and_publish_urls(tmp_path, capsys):
    from datetime import datetime, timedelta, timezone
    from capstone_ota.publisher.bundle import BundleRequest, build_release
    from capstone_ota.common.vehicle_bundle import VehicleBundleManifest
    from capstone_ota.common.signing import verify_document_signature
    from tests.unit.test_vehicle_bundle import bundle_data
    import uuid
    private, public = tmp_path / "private", tmp_path / "public"
    generate_key_pair(private, public)
    payload = tmp_path / "payload"
    make_payload(payload)
    data = bundle_data()
    args = ["vehicle-package", "--spec", str(tmp_path / "spec.json"),
            "--output", str(tmp_path / "out"), "--private-key", str(private),
            "--public-key", str(public), "--base-url", "https://laptop:8443/releases"]
    for prefix, device, target in zip(("central", "cluster"),
            ("central-pi-01", "cluster-pi-02"), data["targets"]):
        now = datetime.now(timezone.utc)
        release = build_release(payload, tmp_path / prefix, BundleRequest(
            device_id=device, version="1.2.3", entrypoint="bin/digital-dash",
            artifact_base_url=f"https://laptop:8443/releases/{prefix}", private_key_path=private,
            job_id=str(uuid.uuid4()), created_at=now, expires_at=now + timedelta(hours=1)))
        target.update(entrypoint=release.manifest.entrypoint, artifact_url=release.manifest.artifact_url,
                      artifact_size=release.manifest.artifact_size, artifact_sha256=release.manifest.artifact_sha256)
        args += [f"--{prefix}-manifest", str(release.manifest_path),
                 f"--{prefix}-signature", str(release.signature_path)]
    (tmp_path / "spec.json").write_text(json.dumps(data))
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    manifest = Path(result["manifest"])
    signature = Path(result["signature"])
    assert manifest.name == "2.0.0.vehicle-manifest.json"
    bundle = VehicleBundleManifest.from_bytes(manifest.read_bytes())
    verify_document_signature(bundle, signature.read_bytes(), public)
    assert {t.release_manifest_url for t in bundle.targets} == {
        "https://laptop:8443/releases/central-control.manifest.json",
        "https://laptop:8443/releases/digital-cluster.manifest.json"}
    sent = []
    class Mqtt:
        def publish_command(self, device, command):
            sent.append((device, command))
    assert main(["vehicle-publish", "--broker", "laptop", "--ca", "ca", "--cert", "cert",
        "--key", "key", "--device-id", "central-pi-01", "--manifest", str(manifest),
        "--signature", str(signature), "--base-url", "https://laptop:8443/releases"],
        mqtt_factory=lambda **kwargs: Mqtt()) == 0
    assert sent == [("central-pi-01", {"schema_version": 1, "command": "vehicle-update",
        "device_id": "central-pi-01", "transaction_id": data["transaction_id"], "bundle_version": "2.0.0",
        "bundle_manifest_url": "https://laptop:8443/releases/2.0.0.vehicle-manifest.json",
        "bundle_signature_url": "https://laptop:8443/releases/2.0.0.vehicle-manifest.sig"})]


@pytest.mark.parametrize("missing", ["--central-manifest", "--central-signature", "--cluster-manifest", "--cluster-signature"])
def test_vehicle_package_requires_independent_release_inputs(missing):
    args = ["vehicle-package", "--spec", "spec", "--output", "out", "--private-key", "key",
            "--public-key", "public", "--base-url", "https://laptop/releases"]
    for flag in ("--central-manifest", "--central-signature", "--cluster-manifest", "--cluster-signature"):
        if flag != missing:
            args += [flag, "file"]
    with pytest.raises(SystemExit):
        main(args)


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
