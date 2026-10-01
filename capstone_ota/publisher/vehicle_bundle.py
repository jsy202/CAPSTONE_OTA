"""Publisher artifacts for vehicle bundle metadata (ECU releases stay separate)."""
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from capstone_ota.common.errors import OtaError
from capstone_ota.common.signing import sign_document
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest
from capstone_ota.publisher.bundle import _atomic_write


@dataclass(frozen=True)
class VehicleBundleArtifacts:
    manifest_path: Path
    signature_path: Path
    manifest: VehicleBundleManifest


def build_vehicle_bundle(
    output_dir: Path, manifest: VehicleBundleManifest, private_key_path: Path,
    retained_tokens: Collection[int] = (),
) -> VehicleBundleArtifacts:
    """Validate and sign canonical metadata, rejecting active/history tokens.

    Callers supply active and retained-history tokens together. Release content
    and signatures are produced separately by the legacy release publisher.
    No clock-dependent fields are generated, so repeated output is identical.
    """
    manifest = VehicleBundleManifest.from_bytes(manifest.canonical_bytes())
    manifest.validate_dependencies()
    if manifest.transaction_token in retained_tokens:
        raise OtaError("TRANSACTION_TOKEN_COLLISION", "transaction token is already retained")
    signature = sign_document(manifest, private_key_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"{manifest.bundle_version}.vehicle-manifest.json"
    signature_path = output_dir / f"{manifest.bundle_version}.vehicle-manifest.sig"
    _atomic_write(manifest_path, manifest.canonical_bytes())
    _atomic_write(signature_path, signature)
    return VehicleBundleArtifacts(manifest_path, signature_path, manifest)
