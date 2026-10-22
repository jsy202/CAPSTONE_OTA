"""A release is three files: metadata, its signature, and the application archive."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_FILES = 4096


def version_key(version: str) -> tuple[int, ...]:
    if (not isinstance(version, str) or len(version) > 48
            or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)):
        raise ValueError("version must be three numeric parts, e.g. 1.0.0")
    return tuple(int(part) for part in version.split("."))


def metadata_check(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {"version", "svn", "sha256"}:
        raise ValueError("invalid release metadata fields")
    version_key(data["version"])
    if type(data["svn"]) is not int or not 1 <= data["svn"] <= 2**31 - 1:
        raise ValueError("metadata svn must be a positive security version")
    if not isinstance(data["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", data["sha256"]):
        raise ValueError("invalid metadata sha256")
    return data


def unique_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON") from exc


def verify(raw: bytes, signature: bytes, public_key: Path) -> dict:
    """Authenticate the exact bytes BEFORE interpreting any metadata field."""
    key = serialization.load_pem_public_key(Path(public_key).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("an Ed25519 public key is required")
    try:
        key.verify(signature, raw)
    except InvalidSignature as exc:
        raise ValueError("release signature verification failed") from exc
    return metadata_check(unique_json(raw))


def app_check(root: Path) -> Path:
    app = root / "bin/app"
    if (root.is_symlink() or (root / "bin").is_symlink() or app.is_symlink()
            or not app.is_file() or not os.access(app, os.X_OK)):
        raise ValueError("payload needs a regular executable bin/app")
    return app


def pack(payload: Path, output: Path, version: str, svn: int, key: Path) -> dict:
    metadata = metadata_check({"version": version, "svn": svn, "sha256": "0" * 64})
    payload, output = Path(payload).absolute(), Path(output).absolute()
    app_check(payload)
    if output.is_relative_to(payload):
        raise ValueError("release output must be outside the payload")
    paths = sorted(payload.rglob("*"))
    if len(paths) > MAX_FILES or any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise ValueError("payload must contain only bounded regular files and directories")
    if sum(p.stat().st_size for p in paths if p.is_file()) > MAX_EXPANDED:
        raise ValueError("payload is too large")
    private = serialization.load_pem_private_key(Path(key).read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise ValueError("an Ed25519 private key is required")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "app.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        for path in paths:
            target.add(path, arcname=path.relative_to(payload).as_posix(), recursive=False)
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("compressed payload is too large")
    metadata["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    raw = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    (output / "release.json").write_bytes(raw)
    (output / "release.sig").write_bytes(private.sign(raw))
    return metadata


def extract(archive: Path, destination: Path) -> None:
    """Reject traversal, links and special files; never use tar.extractall()."""
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            if len(members) > MAX_FILES or sum(m.size for m in members) > MAX_EXPANDED:
                raise ValueError("archive exceeds extraction limits")
            seen = set()
            for member in members:
                name = PurePosixPath(member.name)
                if (not member.name or name.is_absolute() or ".." in name.parts
                        or name.as_posix() != member.name.rstrip("/") or str(name) == "."
                        or member.name in seen or not (member.isfile() or member.isdir())):
                    raise ValueError("unsafe archive entry")
                seen.add(member.name)
            destination.mkdir(parents=True, exist_ok=False)
            for member in members:
                target = destination / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod(0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as data, target.open("xb") as out:
                        while chunk := data.read(65536):
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("cannot extract archive safely") from exc
    app_check(destination)


def tree_hash(root: Path) -> str:
    """Bind recovery to the actual installed files, paths and execute bits."""
    app_check(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("application contains a link or special file")
        name = path.relative_to(root).as_posix().encode()
        digest.update((b"F" if path.is_file() else b"D") + len(name).to_bytes(4, "big") + name)
        digest.update((mode & 0o777).to_bytes(2, "big"))
        if path.is_file():
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                while chunk := stream.read(65536):
                    digest.update(chunk)
    return digest.hexdigest()
