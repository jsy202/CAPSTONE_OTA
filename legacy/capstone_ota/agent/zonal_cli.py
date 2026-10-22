"""capstone-ota-zone-agent: Cluster CAN event loop with 1 s heartbeats."""
from __future__ import annotations

import argparse
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.common.errors import OtaError
from capstone_ota.common.socketcan import SocketCanTransport

from .service_manager import SystemdServiceManager
from .slots import ABSlotInstaller
from .zonal import ZonalAgent
from .zonal_config import ZonalAgentConfig


def run_loop(agent, stop: threading.Event, *, period_s: float = 1.0,
             monotonic: Callable[[], float] = time.monotonic) -> None:
    """Serialize command polling and heartbeat publication on one thread."""
    next_heartbeat = monotonic()
    while not stop.is_set():
        now = monotonic()
        if now >= next_heartbeat:
            agent.publish_heartbeat()
            # Skip missed periods instead of bursting stale heartbeats.
            next_heartbeat = max(next_heartbeat, now) + period_s
        agent.poll_once(max(0.0, min(0.1, next_heartbeat - monotonic())))


def _version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", value) or any(int(p) > 255 for p in value.split(".")):
        raise argparse.ArgumentTypeError("expected numeric x.y.z with uint8 parts")
    return tuple(int(p) for p in value.split("."))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capstone-ota-zone-agent")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--application-socket", required=True, type=Path)
    parser.add_argument("--initial-version", required=True, type=_version,
                        help="provisioned stable Cluster version before the first OTA commit")
    args = parser.parse_args(argv)
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    try:
        config = ZonalAgentConfig.from_json(args.config)
        installer = ABSlotInstaller(config.install_root, SystemdServiceManager(allowed_units={config.service_unit}),
                                    service_unit=config.service_unit)
        with SocketCanTransport(config.can_interface) as transport:
            agent = ZonalAgent(config, installer, transport,
                               application_adapter=ApplicationIpc(args.application_socket),
                               initial_software_version=args.initial_version)
            run_loop(agent, stop)
    except (OtaError, OSError, RuntimeError) as exc:
        print(f"capstone-ota-zone-agent: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
