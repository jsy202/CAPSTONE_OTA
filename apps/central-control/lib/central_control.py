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
