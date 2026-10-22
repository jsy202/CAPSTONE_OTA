from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from capstone_ota.common.errors import OtaError

from .service_manager import ServiceManager


_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")


@dataclass(frozen=True)
class ActivationResult:
    active_version: str
    previous_version: str | None
    healthy: bool
    rolled_back: bool


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise OtaError("RELEASE_INVALID", "release contains an unsupported file type")
        relative = path.relative_to(root).as_posix().encode()
        digest.update(b"D" if path.is_dir() else b"F")
        digest.update(relative)
        digest.update((path.stat().st_mode & 0o777).to_bytes(2, "big"))
        if path.is_file():
            with path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


class ReleaseInstaller:
    def __init__(
        self,
        install_root: Path,
        service_manager: ServiceManager,
        service_unit: str = "digital-dash.service",
        health_timeout: int = 15,
    ):
        self.install_root = Path(install_root)
        self.releases_dir = self.install_root / "releases"
        self.staging_dir = self.install_root / "staging"
        self.current_link = self.install_root / "current"
        self.previous_link = self.install_root / "previous"
        self.service_manager = service_manager
        self.service_unit = service_unit
        self.health_timeout = health_timeout

    @staticmethod
    def _validate_version(version: str) -> None:
        if not _VERSION_RE.fullmatch(version):
            raise OtaError("RELEASE_INVALID", "release version is not safe for a path")

    @staticmethod
    def _validate_entrypoint(root: Path, entrypoint: str) -> Path:
        relative = PurePosixPath(entrypoint)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise OtaError("RELEASE_INVALID", "entrypoint is not a normalized relative path")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
            raise OtaError("RELEASE_INVALID", "entrypoint is missing or not executable")
        return path

    def install_staged(self, version: str, staging_dir: Path, entrypoint: str) -> Path:
        self._validate_version(version)
        staging_dir = Path(staging_dir).resolve()
        self.releases_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        if staging_dir.parent != self.staging_dir.resolve():
            raise OtaError("RELEASE_INVALID", "staging directory is outside the staging root")
        self._validate_entrypoint(staging_dir, entrypoint)
        if staging_dir.stat().st_dev != self.releases_dir.stat().st_dev:
            raise OtaError("RELEASE_INVALID", "staging and releases must share a filesystem")
        target = self.releases_dir / version
        if target.exists():
            if _tree_digest(target) != _tree_digest(staging_dir):
                raise OtaError("RELEASE_CONFLICT", "version already exists with different content")
            shutil.rmtree(staging_dir)
            return target
        try:
            os.replace(staging_dir, target)
        except OSError as exc:
            raise OtaError("RELEASE_INSTALL_FAILED", "cannot promote staged release") from exc
        return target

    def _resolve_link(self, link: Path) -> Path | None:
        if not link.is_symlink():
            return None
        try:
            resolved = link.resolve(strict=True)
        except OSError as exc:
            raise OtaError("ACTIVATION_FAILED", f"broken release link: {link.name}") from exc
        if resolved.parent != self.releases_dir.resolve():
            raise OtaError("ACTIVATION_FAILED", f"release link escapes releases: {link.name}")
        return resolved

    def active_version(self) -> str | None:
        """Return the version selected by the authoritative current symlink."""
        current = self._resolve_link(self.current_link)
        return current.name if current is not None else None

    def _atomic_link(self, target: Path, link: Path) -> None:
        temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}")
        try:
            temporary.symlink_to(target.relative_to(self.install_root))
            os.replace(temporary, link)
        finally:
            temporary.unlink(missing_ok=True)

    def activate(self, version: str) -> ActivationResult:
        self._validate_version(version)
        target = self.releases_dir / version
        if not target.is_dir():
            raise OtaError("RELEASE_INVALID", "release is not installed")
        old = self._resolve_link(self.current_link)
        try:
            if old is not None:
                self._atomic_link(old, self.previous_link)
            self._atomic_link(target, self.current_link)
        except OSError as exc:
            raise OtaError("ACTIVATION_FAILED", "cannot atomically activate release") from exc

        if self.service_manager.restart_and_wait_healthy(self.service_unit, self.health_timeout):
            return ActivationResult(version, old.name if old else None, True, False)

        if old is None:
            self.current_link.unlink(missing_ok=True)
            raise OtaError("HEALTH_CHECK_FAILED", "first release failed its health check")
        try:
            self._atomic_link(old, self.current_link)
        except OSError as exc:
            raise OtaError("ROLLBACK_FAILED", "cannot restore previous release link") from exc
        restored = self.service_manager.restart_and_wait_healthy(
            self.service_unit, self.health_timeout
        )
        if not restored:
            raise OtaError("ROLLBACK_FAILED", "previous release failed its health check")
        return ActivationResult(old.name, version, True, True)

    def rollback(self) -> ActivationResult:
        current = self._resolve_link(self.current_link)
        previous = self._resolve_link(self.previous_link)
        if previous is None:
            raise OtaError("ROLLBACK_UNAVAILABLE", "no previous release is installed")
        try:
            self._atomic_link(previous, self.current_link)
            if current is not None:
                self._atomic_link(current, self.previous_link)
        except OSError as exc:
            raise OtaError("ROLLBACK_FAILED", "cannot switch release links") from exc
        healthy = self.service_manager.restart_and_wait_healthy(
            self.service_unit, self.health_timeout
        )
        if not healthy:
            raise OtaError("ROLLBACK_FAILED", "rolled-back release is unhealthy")
        return ActivationResult(previous.name, current.name if current else None, True, True)
