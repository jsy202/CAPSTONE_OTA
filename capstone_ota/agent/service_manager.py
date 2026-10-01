from __future__ import annotations

import subprocess
import time
import re
from typing import AbstractSet, Callable, Protocol


class ServiceManager(Protocol):
    def restart_and_wait_healthy(self, unit: str, timeout_seconds: int) -> bool: ...


class SystemdServiceManager:
    _UNIT = "digital-dash.service"

    def __init__(
        self,
        runner: Callable = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.5,
        *,
        allowed_units: AbstractSet[str] = frozenset({_UNIT}),
    ):
        self._allowed_units = frozenset(allowed_units)
        if any(not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.service", unit) for unit in self._allowed_units):
            raise ValueError("allowed units must contain safe systemd service names")
        self._runner = runner
        self._clock = clock
        self._sleeper = sleeper
        self._poll_interval = poll_interval

    def _run(self, command: list[str]):
        return self._runner(
            command,
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def restart_and_wait_healthy(self, unit: str, timeout_seconds: int) -> bool:
        if unit not in self._allowed_units:
            raise ValueError("service unit is not explicitly allowed")
        if timeout_seconds <= 0:
            raise ValueError("health timeout must be positive")
        if self._run(["systemctl", "restart", unit]).returncode != 0:
            return False
        startup_deadline = self._clock() + timeout_seconds
        while self._run(["systemctl", "is-active", "--quiet", unit]).returncode != 0:
            remaining = startup_deadline - self._clock()
            if remaining <= 0:
                return False
            self._sleeper(min(self._poll_interval, remaining))

        stability_deadline = self._clock() + timeout_seconds
        while self._clock() < stability_deadline:
            self._sleeper(
                min(self._poll_interval, max(0.0, stability_deadline - self._clock()))
            )
            if self._run(["systemctl", "is-active", "--quiet", unit]).returncode != 0:
                return False
        return True
