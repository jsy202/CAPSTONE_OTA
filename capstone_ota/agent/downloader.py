from __future__ import annotations

import hashlib
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from capstone_ota.common.errors import OtaError


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str


def download_https(
    url: str,
    destination: Path,
    ca_file: Path,
    expected_size: int,
    max_bytes: int,
) -> DownloadResult:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise OtaError("DOWNLOAD_URL_INVALID", "artifact URL must be HTTPS")
    if expected_size <= 0 or max_bytes <= 0:
        raise OtaError("DOWNLOAD_SIZE_MISMATCH", "download sizes must be positive")
    if expected_size > max_bytes:
        raise OtaError("DOWNLOAD_TOO_LARGE", "declared artifact exceeds configured limit")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    total = 0
    declared: int | None = None
    context = ssl.create_default_context(cafile=str(ca_file))
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "capstone-ota/1"})
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            declared_header = response.headers.get("Content-Length")
            if declared_header is not None:
                try:
                    declared = int(declared_header)
                except ValueError as exc:
                    raise OtaError("DOWNLOAD_SIZE_MISMATCH", "invalid Content-Length") from exc
                if declared != expected_size:
                    raise OtaError("DOWNLOAD_SIZE_MISMATCH", "Content-Length differs from manifest")
                if declared > max_bytes:
                    raise OtaError("DOWNLOAD_TOO_LARGE", "response exceeds configured limit")
            with partial.open("xb") as stream:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise OtaError("DOWNLOAD_TOO_LARGE", "download exceeds configured limit")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if total != expected_size:
            code = "DOWNLOAD_FAILED" if declared == expected_size else "DOWNLOAD_SIZE_MISMATCH"
            raise OtaError(code, "download ended before the manifest size was received")
        os.replace(partial, destination)
        return DownloadResult(destination, total, digest.hexdigest())
    except OtaError:
        raise
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise OtaError("DOWNLOAD_TLS_FAILED", "HTTPS certificate verification failed") from exc
        raise OtaError("DOWNLOAD_FAILED", "HTTPS download failed") from exc
    except (ssl.SSLError, OSError) as exc:
        if isinstance(exc, ssl.SSLCertVerificationError):
            raise OtaError("DOWNLOAD_TLS_FAILED", "HTTPS certificate verification failed") from exc
        raise OtaError("DOWNLOAD_FAILED", "HTTPS download failed") from exc
    finally:
        partial.unlink(missing_ok=True)
