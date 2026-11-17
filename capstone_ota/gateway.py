"""Pi1's diagnostic gateway: the OBD-side port that relays maintenance traffic to Pi2.

In a real vehicle, a diagnostic tool never talks to a domain ECU directly; the central
gateway forwards the request. This gateway speaks the same wire format as the maintenance
port (`<slot>\\n<size>\\n<bytes>`, one response line), records every request with its source,
and can be closed after an incident so nothing more reaches Pi2.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time

from .bundle import MAX_ARCHIVE
from .debug_port import DEFAULT_PORT

GATEWAY_PORT = 7400


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(self.server.connection_timeout)
        self.forwarded = False
        source = "%s:%s" % self.client_address[:2]
        slot, size, response = "", 0, ""
        try:
            slot = self.rfile.readline(8).decode().strip()
            size = int(self.rfile.readline(16).decode().strip())
            if not 0 < size <= MAX_ARCHIVE:
                raise ValueError("archive size out of bounds")
            if not self.server.is_open:
                response = "ERROR gateway closed"
                self._drain(size)  # Read and discard: never forwarded, and the client sees a clean answer.
            else:
                response = self._forward(slot, size)
        except (OSError, ValueError) as exc:
            response = "ERROR " + str(exc)
        self.server.record({"event": "request", "source": source, "slot": slot, "size": size,
                            "forwarded": self.forwarded, "response": response})
        try:
            self.wfile.write((response + "\n").encode())
        except OSError:
            pass

    def _drain(self, size: int) -> None:
        remaining = size
        try:
            while remaining and (chunk := self.rfile.read(min(65536, remaining))):
                remaining -= len(chunk)
        except OSError:
            pass

    def _forward(self, slot: str, size: int) -> str:
        with socket.create_connection(self.server.upstream, timeout=self.server.connection_timeout) as upstream:
            self.forwarded = True
            upstream.sendall(slot.encode() + b"\n" + str(size).encode() + b"\n")
            remaining = size
            try:
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("connection closed early")
                    upstream.sendall(chunk)
                    remaining -= len(chunk)
                upstream.shutdown(socket.SHUT_WR)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Upstream already answered (for example an invalid slot); read that answer below.
            response = b""
            while chunk := upstream.recv(4096):
                response += chunk
        return response.decode().strip()


class DiagnosticGateway(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, upstream, log_path, connection_timeout: float = 10.0):
        self.upstream = tuple(upstream)
        self.log_path = Path(log_path)
        self.connection_timeout = connection_timeout
        self._open = threading.Event()
        self._open.set()
        self._log_lock = threading.Lock()
        super().__init__(address, _Handler)

    @property
    def is_open(self) -> bool:
        return self._open.is_set()

    def close(self, reason: str) -> None:
        """Stop relaying. Deliberately one-way: reopening is a service-centre action, not an API."""
        self._open.clear()
        self.record({"event": "gateway_closed", "reason": reason})

    def record(self, entry: dict) -> None:
        line = json.dumps({"time": time.time(), **entry}) + "\n"
        with self._log_lock:
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as log:
                log.write(line)

    def log_tail(self, n: int = 20) -> list[dict]:
        with self._log_lock:
            try:
                lines = self.log_path.read_text().splitlines()
            except FileNotFoundError:
                return []
        return [json.loads(line) for line in lines[-n:]]


def _address(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default=f"0.0.0.0:{GATEWAY_PORT}")
    parser.add_argument("--upstream", default=f"127.0.0.1:{DEFAULT_PORT}")
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args(argv)
    with DiagnosticGateway(_address(args.listen), _address(args.upstream), args.log) as gateway:
        print(f"diagnostic gateway {args.listen} -> {args.upstream}", file=sys.stderr)
        gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
