import json
import stat

import pytest

from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import (
    generate_key_pair,
    sign_manifest,
    verify_manifest_signature,
)


def release(version="1.0.0"):
    return ReleaseManifest.from_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "device_id": "cluster-pi-01",
                "version": version,
                "created_at": "2026-09-29T03:00:00Z",
                "expires_at": "2026-09-29T04:00:00Z",
                "artifact_url": f"https://laptop.local:8443/releases/{version}.tar.gz",
                "artifact_size": 100,
                "artifact_sha256": "a" * 64,
                "entrypoint": "bin/digital-dash",
            }
        ).encode()
    )


def test_generated_key_signs_and_verifies_manifest(tmp_path):
    private_key = tmp_path / "update-private.pem"
    public_key = tmp_path / "update-public.pem"
    generate_key_pair(private_key, public_key)

    signature = sign_manifest(release(), private_key)

    verify_manifest_signature(release(), signature, public_key)
    assert stat.S_IMODE(private_key.stat().st_mode) == 0o600


def test_modified_manifest_is_rejected(tmp_path):
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generate_key_pair(private_key, public_key)

    signature = sign_manifest(release("1.0.0"), private_key)

    with pytest.raises(OtaError) as error:
        verify_manifest_signature(release("1.0.1"), signature, public_key)
    assert error.value.code == "SIGNATURE_INVALID"


def test_modified_signature_and_wrong_key_are_rejected(tmp_path):
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    other_private = tmp_path / "other-private.pem"
    other_public = tmp_path / "other-public.pem"
    generate_key_pair(private_key, public_key)
    generate_key_pair(other_private, other_public)
    signature = sign_manifest(release(), private_key)

    changed_signature = signature[:-1] + bytes([signature[-1] ^ 1])
    for bad_signature, key in ((changed_signature, public_key), (signature, other_public)):
        with pytest.raises(OtaError) as error:
            verify_manifest_signature(release(), bad_signature, key)
        assert error.value.code == "SIGNATURE_INVALID"


def test_key_generation_refuses_to_overwrite_private_key(tmp_path):
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    generate_key_pair(private_key, public_key)

    with pytest.raises(FileExistsError):
        generate_key_pair(private_key, public_key)


def test_generic_signing_remains_interoperable_with_legacy_manifest_api(tmp_path):
    from capstone_ota.common.signing import sign_document, verify_document_signature

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    generate_key_pair(private, public)
    generic = sign_document(release(), private)
    assert generic == sign_manifest(release(), private)
    verify_manifest_signature(release(), generic, public)
    verify_document_signature(release(), sign_manifest(release(), private), public)


def test_generic_document_signature_rejects_tampering_and_wrong_key(tmp_path):
    from capstone_ota.common.signing import sign_document, verify_document_signature

    class Document:
        def __init__(self, content):
            self.content = content

        def canonical_bytes(self):
            return self.content

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    other_private, other_public = tmp_path / "other.pem", tmp_path / "other-public.pem"
    generate_key_pair(private, public)
    generate_key_pair(other_private, other_public)
    original = Document(b'{"value":1}')
    signature = sign_document(original, private)
    verify_document_signature(original, signature, public)
    for document, signed, key in [
        (Document(b'{"value":2}'), signature, public),
        (original, signature[:-1], public), (original, signature, other_public),
    ]:
        with pytest.raises(OtaError) as error:
            verify_document_signature(document, signed, key)
        assert error.value.code == "SIGNATURE_INVALID"
