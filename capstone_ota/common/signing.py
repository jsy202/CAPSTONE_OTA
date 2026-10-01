from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .errors import OtaError
from .manifest import ReleaseManifest


class CanonicalDocument(Protocol):
    """A document whose stable canonical bytes are signed with Ed25519."""

    def canonical_bytes(self) -> bytes: ...


def generate_key_pair(private_path: Path, public_path: Path) -> None:
    private_path = Path(private_path)
    public_path = Path(public_path)
    if private_path.exists() or public_path.exists():
        raise FileExistsError("refusing to overwrite an existing update key")
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.touch(mode=0o600, exist_ok=False)
    private_path.write_bytes(private_bytes)
    private_path.chmod(0o600)
    public_path.write_bytes(public_bytes)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise OtaError("SIGNING_KEY_INVALID", "cannot load Ed25519 private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise OtaError("SIGNING_KEY_INVALID", "private key is not Ed25519")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(Path(path).read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise OtaError("SIGNATURE_INVALID", "cannot load trusted public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise OtaError("SIGNATURE_INVALID", "trusted key is not Ed25519")
    return key


def sign_document(document: CanonicalDocument, private_key_path: Path) -> bytes:
    return _load_private_key(private_key_path).sign(document.canonical_bytes())


def verify_document_signature(
    document: CanonicalDocument, signature: bytes, public_key_path: Path
) -> None:
    try:
        _load_public_key(public_key_path).verify(signature, document.canonical_bytes())
    except OtaError:
        raise
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise OtaError("SIGNATURE_INVALID", "document signature is invalid") from exc


def sign_manifest(manifest: ReleaseManifest, private_key_path: Path) -> bytes:
    return sign_document(manifest, private_key_path)


def verify_manifest_signature(
    manifest: ReleaseManifest, signature: bytes, public_key_path: Path
) -> None:
    verify_document_signature(manifest, signature, public_key_path)
