from __future__ import annotations

import subprocess
import time
from typing import Callable, Protocol


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
    ):
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
        if unit != self._UNIT:
            raise ValueError("only digital-dash.service may be controlled")
        if timeout_seconds <= 0:
            raise ValueError("health timeout must be positive")
        if self._run(["systemctl", "restart", self._UNIT]).returncode != 0:
            return False
        deadline = self._clock() + timeout_seconds
        while self._clock() < deadline:
            if self._run(["systemctl", "is-active", "--quiet", self._UNIT]).returncode == 0:
                return True
            self._sleeper(min(self._poll_interval, max(0.0, deadline - self._clock())))
        return False
