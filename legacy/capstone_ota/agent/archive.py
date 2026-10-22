from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from capstone_ota.common.errors import OtaError


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int
    max_expanded_bytes: int


def safe_extract_tar(archive: Path, destination: Path, limits: ArchiveLimits) -> None:
    if limits.max_files <= 0 or limits.max_expanded_bytes <= 0:
        raise OtaError("ARCHIVE_LIMIT_EXCEEDED", "archive limits must be positive")
    archive = Path(archive)
    destination = Path(destination)
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            seen: set[str] = set()
            file_count = 0
            expanded_size = 0
            for member in members:
                name = PurePosixPath(member.name)
                if (
                    not member.name
                    or name.is_absolute()
                    or any(part in {"", ".", ".."} for part in name.parts)
                    or member.name in seen
                    or not (member.isfile() or member.isdir())
                ):
                    raise OtaError("ARCHIVE_UNSAFE", f"unsafe archive member: {member.name}")
                seen.add(member.name)
                if member.isfile():
                    file_count += 1
                    expanded_size += member.size
                if file_count > limits.max_files or expanded_size > limits.max_expanded_bytes:
                    raise OtaError("ARCHIVE_LIMIT_EXCEEDED", "archive exceeds extraction limits")

            destination.mkdir(parents=True, exist_ok=False)
            root = destination.resolve()
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                resolved_parent = target.parent.resolve()
                if resolved_parent != root and root not in resolved_parent.parents:
                    raise OtaError("ARCHIVE_UNSAFE", "archive path escapes destination")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    target.chmod((member.mode & 0o777) | 0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = source.extractfile(member)
                if extracted is None:
                    raise OtaError("ARCHIVE_UNSAFE", "regular file has no content")
                with extracted, target.open("xb") as output:
                    while True:
                        chunk = extracted.read(64 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                target.chmod(member.mode & 0o777)
    except OtaError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise OtaError("ARCHIVE_INVALID", "cannot safely extract release archive") from exc
