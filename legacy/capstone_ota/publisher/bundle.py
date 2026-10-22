from __future__ import annotations

import gzip
import hashlib
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import sign_manifest


@dataclass(frozen=True)
class BundleRequest:
    device_id: str
    version: str
    entrypoint: str
    artifact_base_url: str
    private_key_path: Path
    job_id: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ReleaseBundle:
    archive_path: Path
    manifest_path: Path
    signature_path: Path
    manifest: ReleaseManifest


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OtaError("INVALID_MANIFEST", "release timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _payload_entries(payload_dir: Path) -> list[Path]:
    entries: list[Path] = []
    for path in sorted(payload_dir.rglob("*"), key=lambda item: item.relative_to(payload_dir).as_posix()):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise OtaError("INVALID_PAYLOAD", f"unsupported payload entry: {path.name}")
        entries.append(path)
    return entries


def _write_archive(payload_dir: Path, destination: Path) -> None:
    entries = _payload_entries(payload_dir)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in entries:
                    relative = path.relative_to(payload_dir).as_posix()
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if info.isdir():
                        info.mode = 0o755
                        archive.addfile(info)
                    else:
                        info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                        with path.open("rb") as source:
                            archive.addfile(info, source)


def _atomic_write(path: Path, content: bytes, mode: int = 0o644) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_release(
    payload_dir: Path, output_dir: Path, request: BundleRequest
) -> ReleaseBundle:
    payload_dir = Path(payload_dir).resolve()
    output_dir = Path(output_dir).resolve()
    entrypoint = payload_dir / request.entrypoint
    if (
        not payload_dir.is_dir()
        or not entrypoint.is_file()
        or entrypoint.is_symlink()
        or not os.access(entrypoint, os.X_OK)
    ):
        raise OtaError("INVALID_PAYLOAD", "payload entrypoint is missing or not executable")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{request.version}.tar.gz"
    manifest_path = output_dir / f"{request.version}.manifest.json"
    signature_path = output_dir / f"{request.version}.manifest.sig"
    archive_tmp = output_dir / f".{request.version}.tar.gz.part"
    try:
        _write_archive(payload_dir, archive_tmp)
        os.replace(archive_tmp, archive_path)
    finally:
        archive_tmp.unlink(missing_ok=True)

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = ReleaseManifest.from_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": request.job_id,
                "device_id": request.device_id,
                "version": request.version,
                "created_at": _timestamp(request.created_at),
                "expires_at": _timestamp(request.expires_at),
                "artifact_url": f"{request.artifact_base_url.rstrip('/')}/{request.version}.tar.gz",
                "artifact_size": archive_path.stat().st_size,
                "artifact_sha256": digest,
                "entrypoint": request.entrypoint,
            }
        ).encode("utf-8")
    )
    signature = sign_manifest(manifest, request.private_key_path)
    _atomic_write(manifest_path, manifest.canonical_bytes())
    _atomic_write(signature_path, signature)
    return ReleaseBundle(archive_path, manifest_path, signature_path, manifest)
