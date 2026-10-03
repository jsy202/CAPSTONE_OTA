"""Central Control demonstration application for the zonal OTA (ZONAL_OTA_GUIDE §5).

This is the OTA *target* application on the Central HPC, not part of the OTA
framework. It implements exactly the contract the coordinator verifies:

* 0x100 heartbeat (default 1 s) with the running application's identity,
  taken from the sanitized status file written by capstone-ota-ui-status
  (never hard-coded, never from the privileged slot journal);
* 0x200 vehicle status (default 100 ms) from a deterministic drive profile;
* /run/central-control/ota.sock `maintenance` IPC (ApplicationIpc v1).

Maintenance places vehicle output into a stationary safe profile; both CAN
streams keep flowing because trial and recovery verification observe them.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import socket
import stat
import time
from pathlib import Path
from typing import Callable, Protocol

from capstone_ota.agent.ui_status import read_previous
from capstone_ota.common.can_protocol import ApplicationState, CanFrame, Gear, HeartbeatFrame, VehicleStatusFrame


PROFILE_CYCLE_S = 20.0
MAINTENANCE_PROFILE = (0, 800, Gear.PARK, 0)
_STATES = {"stable": ApplicationState.STABLE, "trial": ApplicationState.TRIAL}


class Transport(Protocol):
    def send(self, frame: CanFrame) -> None: ...


def profile(t: float) -> tuple[int, int, Gear, int]:
    """Deterministic 20 s drive cycle in wire units (speed 0.1 km/h, rpm)."""
    phase = t % PROFILE_CYCLE_S
    if phase < 3.0:
        return 0, 800, Gear.PARK, 0
    if phase < 10.0:
        frac = (phase - 3.0) / 7.0
        return round(800 * frac), round(1500 + 1500 * frac), Gear.DRIVE, 0
    if phase < 15.0:
        return 800, 2500, Gear.DRIVE, 0
    frac = (phase - 15.0) / 5.0
    return round(800 * (1.0 - frac)), round(2500 - 1600 * frac), Gear.DRIVE, 0


def read_identity(path: Path) -> tuple[ApplicationState, tuple[int, int, int]] | None:
    """Running application identity, or None when it cannot be established."""
    status = read_previous(Path(path))
    if status is None or status["state"] not in _STATES or not isinstance(status["version"], str):
        return None
    parts = status["version"].split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    version = tuple(int(p) for p in parts)
    if any(v > 255 for v in version):
        return None
    return _STATES[status["state"]], version


class CentralControl:
    """Frame scheduler; tick(now) is pure with respect to the injected time."""

    def __init__(self, transport: Transport, identity_path: Path, *, heartbeat_period: float = 1.0,
                 vehicle_period: float = 0.1, protocol: tuple[int, int] = (1, 0),
                 start: float | None = None, clock: Callable[[], float] = time.monotonic):
        if not (heartbeat_period > 0 and vehicle_period > 0):
            raise ValueError("periods must be positive")
        self.transport, self.identity_path, self.protocol = transport, Path(identity_path), protocol
        self.heartbeat_period, self.vehicle_period = heartbeat_period, vehicle_period
        self.start = clock() if start is None else start
        self._next_heartbeat = self._next_vehicle = self.start
        self._heartbeat_counter = self._vehicle_counter = 0
        self.maintenance = False

    def set_maintenance(self, enabled: bool) -> None:
        self.maintenance = bool(enabled)

    @staticmethod
    def _advance(due: float, period: float, now: float) -> float:
        due += period
        # After a stall resynchronise instead of bursting the missed frames.
        return now + period if due <= now else due

    def tick(self, now: float) -> float:
        if now >= self._next_heartbeat:
            identity = read_identity(self.identity_path)
            if identity is not None:
                state, version = identity
                self.transport.send(HeartbeatFrame("central-control", version, *self.protocol, state,
                                                   self._heartbeat_counter).encode())
                self._heartbeat_counter = (self._heartbeat_counter + 1) % 256
            self._next_heartbeat = self._advance(self._next_heartbeat, self.heartbeat_period, now)
        if now >= self._next_vehicle:
            speed, rpm, gear, warnings = MAINTENANCE_PROFILE if self.maintenance else profile(now - self.start)
            self.transport.send(VehicleStatusFrame(speed, rpm, gear, warnings, self._vehicle_counter).encode())
            self._vehicle_counter = (self._vehicle_counter + 1) % 256
            self._next_vehicle = self._advance(self._next_vehicle, self.vehicle_period, now)
        return min(self._next_heartbeat, self._next_vehicle)


# --- maintenance IPC (ApplicationIpc v1 server side) ------------------------

MAX_MESSAGE = 4096
CONNECTION_DEADLINE_S = 2.0
_REQUEST_ID = re.compile(r"[0-9a-f]{1,64}\Z")
_ENVELOPE = {"schema_version", "request_id", "operation", "payload"}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reply(request_id: str, ok: bool, result: dict) -> bytes:
    return json.dumps({"schema_version": 1, "request_id": request_id, "ok": ok, "result": result},
                      separators=(",", ":")).encode() + b"\n"


def handle_request(raw: bytes, app: CentralControl) -> bytes | None:
    """Answer one request line; None means close without a response."""
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        return None
    try:
        request = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, ValueError):
        return None
    if (not isinstance(request, dict) or set(request) != _ENVELOPE
            or type(request["schema_version"]) is not int or request["schema_version"] != 1
            or not isinstance(request["request_id"], str) or not _REQUEST_ID.fullmatch(request["request_id"])):
        return None
    payload = request["payload"]
    if (request["operation"] != "maintenance" or not isinstance(payload, dict)
            or set(payload) != {"enabled"} or type(payload["enabled"]) is not bool):
        return _reply(request["request_id"], False, {})
    app.set_maintenance(payload["enabled"])
    return _reply(request["request_id"], True, {"enabled": app.maintenance})


class MaintenanceServer:
    """Non-blocking Unix socket server driven by the application's selector loop.

    The socket is 0660 so only the application account and its group (the
    coordinator's supplementary group) can connect. An existing path is only
    replaced when it is a stale socket; any other file type aborts startup.
    """

    def __init__(self, path: Path, app: CentralControl, *, clock: Callable[[], float] = time.monotonic):
        self.path, self.app, self.clock = Path(path), app, clock
        try:
            mode = os.lstat(self.path).st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None:
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to replace non-socket {self.path}")
            self.path.unlink()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o117)
        try:
            self.sock.bind(str(self.path))
        finally:
            os.umask(old)
        os.chmod(self.path, 0o660)
        self.sock.listen(8)
        self.sock.setblocking(False)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.sock, selectors.EVENT_READ, None)
        self.connections: dict[socket.socket, tuple[bytearray, float]] = {}

    def _drop(self, conn: socket.socket) -> None:
        self.selector.unregister(conn)
        self.connections.pop(conn, None)
        conn.close()

    def service(self, timeout: float) -> None:
        """Handle ready events for at most timeout seconds; never blocks on a client."""
        for key, _ in self.selector.select(max(0.0, timeout)):
            if key.data is None:
                try:
                    conn, _ = self.sock.accept()
                except BlockingIOError:
                    continue
                conn.setblocking(False)
                self.connections[conn] = (bytearray(), self.clock() + CONNECTION_DEADLINE_S)
                self.selector.register(conn, selectors.EVENT_READ, "client")
                continue
            conn = key.fileobj
            buffer, _ = self.connections[conn]
            try:
                chunk = conn.recv(MAX_MESSAGE + 1 - len(buffer))
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                self._drop(conn)
                continue
            buffer += chunk
            if not chunk or b"\n" in buffer or len(buffer) > MAX_MESSAGE:
                line = bytes(buffer[:buffer.index(b"\n") + 1]) if b"\n" in buffer else bytes(buffer)
                reply = handle_request(line, self.app) if chunk else None
                if reply is not None:
                    try:
                        conn.sendall(reply)
                    except OSError:
                        pass
                self._drop(conn)
        now = self.clock()
        for conn, (_, deadline) in list(self.connections.items()):
            if now >= deadline:
                self._drop(conn)

    def close(self) -> None:
        for conn in list(self.connections):
            self._drop(conn)
        self.selector.close()
        self.sock.close()
        try:
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):
                self.path.unlink()
        except FileNotFoundError:
            pass
