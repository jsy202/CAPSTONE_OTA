"""capstone-ota-coordinator: Central HPC service entry point."""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from pathlib import Path

from capstone_ota.agent.archive import ArchiveLimits
from capstone_ota.agent.service_manager import SystemdServiceManager
from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.common.errors import OtaError
from capstone_ota.common.socketcan import SocketCanTransport
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest

from .can_adapters import CanBus, CanCompatibilityProbe, CanZoneClient
from .config import CoordinatorConfig
from .core import VehicleCoordinator
from .runtime import ArtifactCache, CoordinatorMqtt, CoordinatorRuntime, VehicleCommandHandler, create_cache_server


def unit_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False, timeout=5,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def build_runtime(config: CoordinatorConfig) -> CoordinatorRuntime:
    installer = ABSlotInstaller(config.install_root, SystemdServiceManager(allowed_units={config.service_unit}),
                                service_unit=config.service_unit)
    bus = CanBus(SocketCanTransport(config.can_interface))
    mqtt = CoordinatorMqtt(device_id=config.device_id, host=config.broker_host, port=config.broker_port,
                           ca_file=config.ca_file, cert_file=config.client_cert, key_file=config.client_key)
    application = ApplicationIpc(config.application_socket)
    coordinator: VehicleCoordinator | None = None

    def state():
        return coordinator.state

    def stable_version(role: str) -> str | None:
        baseline = state().stable_bundle or {"targets": []}
        return next((t["software_version"] for t in baseline["targets"] if t["ecu_id"] == role), None)

    def publish(event: dict) -> None:
        mqtt.publish_status({**event, "device_id": config.device_id})

    cache = ArtifactCache(config.cache_root, ca_file=config.ca_file, public_key=config.public_key,
                          max_bytes=config.download_max_bytes,
                          limits=ArchiveLimits(config.archive_max_files, config.archive_max_bytes),
                          current_version=stable_version)
    coordinator = VehicleCoordinator(
        state_path=config.state_file, installer=installer, zone=CanZoneClient(bus, state),
        probe=CanCompatibilityProbe(bus, lambda: unit_active(config.service_unit)),
        prepare_artifact=cache.prepare, publish_artifacts=cache.publish,
        stable_bundle=VehicleBundleManifest.from_bytes(config.stable_bundle.read_bytes()),
        maintenance=application.set_maintenance, progress=publish)
    handler = VehicleCommandHandler(coordinator, device_id=config.device_id, ca_file=config.ca_file,
                                    public_key=config.public_key, publish_status=publish)
    server = create_cache_server(config.cache_root, config.https_bind, config.https_port,
                                 config.https_cert, config.https_key)
    busy = OtaError("BUSY", "another vehicle update is already queued")
    return CoordinatorRuntime(coordinator, mqtt, server, bus, handler,
                              on_busy=lambda: handler._reject(None, busy))


def serve(runtime, stop: threading.Event, interval_s: float = 1.0) -> int:
    try:
        runtime.start()
        while not stop.wait(interval_s):
            runtime.check()
        return 0
    except Exception as exc:
        print(f"capstone-ota-coordinator: {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capstone-ota-coordinator")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    stop = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stop.set())
    try:
        runtime = build_runtime(CoordinatorConfig.from_json(args.config))
    except (OtaError, OSError) as exc:
        print(f"capstone-ota-coordinator: {exc}", file=sys.stderr)
        return 2
    return serve(runtime, stop)


if __name__ == "__main__":
    sys.exit(main())
