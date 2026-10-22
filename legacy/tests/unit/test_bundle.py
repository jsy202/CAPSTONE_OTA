import hashlib
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from capstone_ota.common.errors import OtaError
from capstone_ota.common.signing import generate_key_pair, verify_manifest_signature
from capstone_ota.publisher.bundle import BundleRequest, build_release


def request(private_key: Path, version="1.2.3"):
    return BundleRequest(
        device_id="cluster-pi-01",
        version=version,
        entrypoint="bin/digital-dash",
        artifact_base_url="https://laptop.local:8443/releases",
        private_key_path=private_key,
        job_id="550e8400-e29b-41d4-a716-446655440000",
        created_at=datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc),
    )


def make_payload(root: Path):
    executable = root / "bin" / "digital-dash"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"#!/bin/sh\necho dashboard\n")
    executable.chmod(0o755)
    (root / "qml").mkdir()
    (root / "qml" / "Main.qml").write_text("import QtQuick\nItem {}\n", encoding="utf-8")


def test_bundle_contains_relative_regular_payload_and_matches_manifest(tmp_path):
    payload = tmp_path / "payload"
    output = tmp_path / "releases"
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    make_payload(payload)
    generate_key_pair(private_key, public_key)

    bundle = build_release(payload, output, request(private_key))

    assert bundle.archive_path.name == "1.2.3.tar.gz"
    assert bundle.manifest_path.name == "1.2.3.manifest.json"
    assert bundle.signature_path.name == "1.2.3.manifest.sig"
    assert bundle.manifest.artifact_size == bundle.archive_path.stat().st_size
    assert bundle.manifest.artifact_sha256 == hashlib.sha256(bundle.archive_path.read_bytes()).hexdigest()
    verify_manifest_signature(bundle.manifest, bundle.signature_path.read_bytes(), public_key)
    assert bundle.manifest_path.read_bytes() == bundle.manifest.canonical_bytes()

    with tarfile.open(bundle.archive_path, "r:gz") as archive:
        names = archive.getnames()
        assert names == ["bin", "bin/digital-dash", "qml", "qml/Main.qml"]
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
        assert all(member.isfile() or member.isdir() for member in archive.getmembers())


def test_bundle_archive_is_reproducible_for_same_request(tmp_path):
    payload = tmp_path / "payload"
    make_payload(payload)
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generate_key_pair(private_key, public_key)

    first = build_release(payload, tmp_path / "out-a", request(private_key))
    second = build_release(payload, tmp_path / "out-b", request(private_key))

    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    assert first.manifest.canonical_bytes() == second.manifest.canonical_bytes()
    assert first.signature_path.read_bytes() == second.signature_path.read_bytes()


@pytest.mark.parametrize("mode", [None, 0o644])
def test_bundle_rejects_missing_or_non_executable_entrypoint(tmp_path, mode):
    payload = tmp_path / "payload"
    payload.mkdir()
    if mode is not None:
        entrypoint = payload / "bin" / "digital-dash"
        entrypoint.parent.mkdir()
        entrypoint.write_text("not executable")
        entrypoint.chmod(mode)
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generate_key_pair(private_key, public_key)

    with pytest.raises(OtaError) as error:
        build_release(payload, tmp_path / "out", request(private_key))

    assert error.value.code == "INVALID_PAYLOAD"


def test_bundle_rejects_symlink_in_payload(tmp_path):
    payload = tmp_path / "payload"
    make_payload(payload)
    os.symlink("Main.qml", payload / "qml" / "Alias.qml")
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generate_key_pair(private_key, public_key)

    with pytest.raises(OtaError) as error:
        build_release(payload, tmp_path / "out", request(private_key))

    assert error.value.code == "INVALID_PAYLOAD"
