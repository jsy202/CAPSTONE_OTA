from __future__ import annotations

import mimetypes
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ALLOWED_SUFFIXES = (".tar.gz", ".manifest.json", ".manifest.sig", ".vehicle-manifest.json", ".vehicle-manifest.sig")


def _handler_for(release_root: Path):
    root = release_root.resolve(strict=True)

    class ReleaseHandler(BaseHTTPRequestHandler):
        def _release_path(self) -> Path | None:
            raw_path = unquote(urlsplit(self.path).path)
            if not raw_path.startswith("/releases/"):
                return None
            filename = raw_path.removeprefix("/releases/")
            if not filename or "/" in filename or filename in {".", ".."}:
                return None
            if not filename.endswith(_ALLOWED_SUFFIXES):
                return None
            candidate = root / filename
            if candidate.is_symlink() or not candidate.is_file():
                return None
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                return None
            if resolved.parent != root:
                return None
            return resolved

        def _send_release(self, include_body: bool) -> None:
            path = self._release_path()
            if path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            size = path.stat().st_size
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                with path.open("rb") as source:
                    while chunk := source.read(64 * 1024):
                        self.wfile.write(chunk)

        def do_GET(self) -> None:
            self._send_release(True)

        def do_HEAD(self) -> None:
            self._send_release(False)

        def log_message(self, _format, *_args) -> None:
            return

    return ReleaseHandler


def create_https_server(
    release_root: Path,
    bind: str,
    port: int,
    cert_file: Path,
    key_file: Path,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), _handler_for(Path(release_root)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
