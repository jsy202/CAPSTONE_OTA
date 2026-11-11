"""An always-open recall-service maintenance port.

Stands in for the real-world practice this project is modeling: a recall opens a
diagnostic/maintenance port on the vehicle to service it, and the port is sometimes
left open afterward. It is unauthenticated on purpose -- that absence is the attack
surface being demonstrated, not a gap meant to be patched here.

It can do exactly one thing: replace the contents of one named A/B slot directory
under a device root from an uploaded tar.gz app archive (the same archive shape
bundle.pack() produces as app.tar.gz, extracted with the same safe extractor the real
OTA pipeline uses). It never touches state.json, trusted.pub, current, or .lock --
not by convention, but because `slot_path` only ever resolves inside
`<device_root>/slots/<A|B>`, and nothing in this module opens any other path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import shutil
import socket
import socketserver
import sys
import tempfile

from .bundle import MAX_ARCHIVE, extract

DEFAULT_PORT = 7312


def slot_path(root: Path, name: str) -> Path:
    """The only path this service is ever allowed to touch."""
    if name not in ("A", "B"):
        raise ValueError("slot must be A or B")
    root = Path(root).resolve()
    slots = root / "slots"
    target = (slots / name).resolve()
    if target.parent != slots.resolve() or target.name != name:
        raise ValueError("slot path escapes the device's slots directory")
    return target


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        # An always-open, unauthenticated, one-thread-per-connection service must bound
        # how long one stalled client can tie up a thread, or enough of them stop the
        # port from accepting the next (real) connection.
        self.connection.settimeout(self.server.connection_timeout)
        try:
            name = self.rfile.readline(8).decode().strip()
            target = slot_path(self.server.device_root, name)
            size = int(self.rfile.readline(16).decode().strip())
            if not 0 < size <= MAX_ARCHIVE:
                raise ValueError("archive size out of bounds")
            with tempfile.TemporaryDirectory(prefix=".debug-port-", dir=self.server.device_root) as staging:
                archive = Path(staging) / "payload.tar.gz"
                remaining = size
                with archive.open("wb") as out:
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("connection closed early")
                        out.write(chunk)
                        remaining -= len(chunk)
                replacement = Path(staging) / "slot"
                extract(archive, replacement)  # same safe extractor the real OTA pipeline uses
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target)
                os.replace(replacement, target)
            self.wfile.write(b"OK\n")
        except (OSError, ValueError) as exc:
            try:
                self.wfile.write(("ERROR " + str(exc) + "\n").encode())
            except OSError:
                pass  # the stalled/closed connection that got us here can't be told anyway


class DebugPortServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, device_root, connection_timeout: float = 10.0):
        self.device_root = Path(device_root).resolve()
        self.connection_timeout = connection_timeout
        super().__init__(address, _Handler)


def push(host: str, port: int, slot: str, archive_path: Path, timeout: float = 10.0) -> str:
    """The attack side: push an app.tar.gz (any validly-packed release, outdated or not)
    into a slot through the open port. No credentials -- there is nothing to supply."""
    data = Path(archive_path).read_bytes()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(slot.encode() + b"\n" + str(len(data)).encode() + b"\n" + data)
        sock.shutdown(socket.SHUT_WR)
        response = b""
        while chunk := sock.recv(4096):
            response += chunk
    return response.decode().strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-root", required=True, type=Path)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    with DebugPortServer((args.bind, args.port), args.device_root) as server:
        print(f"debug port listening on {args.bind}:{args.port} -> {args.device_root}", file=sys.stderr)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
