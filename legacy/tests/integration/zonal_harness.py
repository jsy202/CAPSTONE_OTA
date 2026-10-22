"""No-hardware two-ECU harness: real TLS, real slots, real agent, virtual CAN.

Only the physical bus and the two Qt applications are simulated. The virtual
bus delivers each frame to every other port, like SocketCAN loopback. The
application simulators derive behavior from the files installed in their
active A/B slot, so a rollback restores the previous behavior for real.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from capstone_ota.agent.archive import ArchiveLimits
from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.agent.zonal import ApplicationObservation, ZonalAgent
from capstone_ota.agent.zonal_cli import run_loop
from capstone_ota.agent.zonal_config import ZonalAgentConfig
from capstone_ota.common.can_protocol import ApplicationState, HeartbeatFrame, Gear, VehicleStatusFrame
from capstone_ota.common.signing import generate_key_pair
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest
from capstone_ota.coordinator.can_adapters import CanBus, CanCompatibilityProbe, CanZoneClient
from capstone_ota.coordinator.compatibility import CanContract, CompatibilityValidator
from capstone_ota.coordinator.core import VehicleCoordinator
from capstone_ota.coordinator.runtime import ArtifactCache, VehicleCommandHandler, create_cache_server
from capstone_ota.publisher.bundle import BundleRequest, build_release
from capstone_ota.publisher.cli import main as publisher_main
from capstone_ota.publisher.http_server import create_https_server
from tests.integration.test_https_artifact_server import server_certificate


CONTRACT = CanContract(heartbeat_period_s=0.1, vehicle_period_s=0.05)
POLICY = {"prepare_timeout_s": 30, "activation_timeout_s": 20, "verification_timeout_s": 10,
          "heartbeat_timeout_s": 1, "max_missed_heartbeats": 5, "max_error_count": 0}


class VirtualCan:
    def __init__(self):
        self.ports: list[VirtualPort] = []
        self.drop = lambda frame: False

    def port(self) -> "VirtualPort":
        port = VirtualPort(self)
        self.ports.append(port)
        return port


class VirtualPort:
    def __init__(self, bus: VirtualCan):
        self.bus, self.frames = bus, queue.Queue()

    def open(self):
        pass

    def close(self):
        pass

    def send(self, frame):
        if not self.bus.drop(frame):
            for port in list(self.bus.ports):
                if port is not self:
                    port.frames.put(frame)

    def recv(self, timeout):
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None


class Services:
    def restart_and_wait_healthy(self, unit, timeout_seconds):
        return True


def _app_version(installer: ABSlotInstaller) -> tuple[ApplicationState, tuple[int, int, int]]:
    state = installer.state
    trial = state.trial_slot is not None and state.active_slot == state.trial_slot
    version = (state.trial_version if trial else state.stable_version) or "1.0.0"
    return ApplicationState.TRIAL if trial else ApplicationState.STABLE, tuple(int(p) for p in version.split("."))


class CentralApp:
    """Central application double: 0x200 every 50 ms and 0x100 every 100 ms."""
    def __init__(self, port: VirtualPort, installer: ABSlotInstaller):
        self.port, self.installer = port, installer
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        tick = 0
        next_at = time.monotonic()
        while not self.stop.is_set():
            self.port.send(VehicleStatusFrame(650, 3000, Gear.DRIVE, 0, tick % 256).encode())
            if tick % 2 == 0:
                state, version = _app_version(self.installer)
                self.port.send(HeartbeatFrame("central-control", version, 1, 0, state, (tick // 2) % 256).encode())
            tick += 1
            next_at += 0.05
            self.stop.wait(max(0.0, next_at - time.monotonic()))


class ClusterApp:
    """Cluster application double; interpretation comes from the active slot."""
    def __init__(self, installer: ABSlotInstaller):
        self.installer = installer

    def apply_vehicle_status(self, sample):
        self.last = sample

    def observe_functional_test(self, request):
        behavior = self.installer.active_link / "behavior.json"
        divisor = json.loads(behavior.read_text())["speed_divisor"] if behavior.exists() else 1
        return ApplicationObservation(request.speed // divisor, request.rpm, request.gear, request.warnings)


def _bundle_dict(transaction_id, versions, now, base_url, releases=None):
    targets = []
    for role, version in versions.items():
        release = (releases or {}).get(role)
        targets.append({
            "ecu_id": role, "hardware_id": "rpi-4b", "software_version": version,
            "entrypoint": "bin/application", "can_interface": "vehicle-status",
            "protocol_major": 1, "protocol_minor": 0, "capabilities": ["speed", "gear"],
            "release_manifest_url": f"{base_url}/{role}.manifest.json",
            "release_signature_url": f"{base_url}/{role}.manifest.sig",
            "artifact_url": release.artifact_url if release else f"{base_url}/{role}.tar.gz",
            "artifact_size": release.artifact_size if release else 1,
            "artifact_sha256": release.artifact_sha256 if release else "0" * 64,
        })
    stamp = lambda t: t.isoformat().replace("+00:00", "Z")
    return {"schema_version": 1, "transaction_id": transaction_id, "bundle_version": "2.0.0",
            "created_at": stamp(now - timedelta(minutes=1)), "expires_at": stamp(now + timedelta(hours=1)),
            "targets": targets, "dependencies": [], "rollback_scope": "all", "health_policy": POLICY}


class Vehicle:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.ca, cert, key = server_certificate(tmp_path)
        self.signing, self.public = tmp_path / "update.key", tmp_path / "update.pub"
        generate_key_pair(self.signing, self.public)
        self.release_root = tmp_path / "laptop-releases"
        self.release_root.mkdir()
        self.laptop = create_https_server(self.release_root, "127.0.0.1", 0, cert, key)
        self.base = f"https://127.0.0.1:{self.laptop.server_port}/releases"
        self.cache_root = tmp_path / "central-cache"
        self.cache_root.mkdir()
        self.cache_server = create_cache_server(self.cache_root, "127.0.0.1", 0, cert, key)
        self.servers = [self.laptop, self.cache_server]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.can = VirtualCan()
        self.events, self.maintenance, self.rejections = [], [], []
        stable = _bundle_dict(str(uuid.uuid4()), {"central-control": "1.0.0", "digital-cluster": "1.0.0"},
                              datetime.now(timezone.utc), self.base)
        self.stable_bundle = VehicleBundleManifest.from_bytes(json.dumps(stable).encode())
        self.cluster_runner = None
        self.boot()

    # -- process lifecycle -------------------------------------------------
    def boot(self):
        """Start (or restart after power loss) every process on both Pis."""
        self.central_installer = ABSlotInstaller(self.tmp / "central", Services(), service_unit="central-control.service")
        self.cluster_installer = ABSlotInstaller(self.tmp / "cluster", Services())
        self.central_app = CentralApp(self.can.port(), self.central_installer)
        self.central_app.thread.start()
        config = ZonalAgentConfig("cluster-pi-02", "digital-cluster", "can0", 500000,
            f"https://127.0.0.1:{self.cache_server.server_port}", self.ca, self.public,
            self.tmp / "cluster", self.tmp / "cluster-agent" / "tokens.json", "digital-dash.service",
            10_000_000, 100, 10_000_000)
        self.cluster_app = ClusterApp(self.cluster_installer)
        self.agent = ZonalAgent(config, self.cluster_installer, self.can.port(),
                                application_adapter=self.cluster_app, initial_software_version=(1, 0, 0))
        self.cluster_stop = threading.Event()
        self.cluster_runner = threading.Thread(target=run_loop, args=(self.agent, self.cluster_stop),
                                               kwargs={"period_s": 0.1}, daemon=True)
        self.cluster_runner.start()
        self.bus = CanBus(self.can.port())
        self.bus.start()
        cache = ArtifactCache(self.cache_root, ca_file=self.ca, public_key=self.public, max_bytes=10_000_000,
                              limits=ArchiveLimits(100, 10_000_000), current_version=self._stable_version)
        self.coordinator = VehicleCoordinator(
            state_path=self.tmp / "coordinator" / "vehicle.json", installer=self.central_installer,
            zone=CanZoneClient(self.bus, lambda: self.coordinator.state),
            probe=CanCompatibilityProbe(self.bus, lambda: True, window_s=1.0, min_window_s=0.5),
            prepare_artifact=cache.prepare, publish_artifacts=cache.publish, stable_bundle=self.stable_bundle,
            maintenance=lambda enabled, timeout_s: self.maintenance.append(enabled),
            progress=self.events.append, validator=CompatibilityValidator(CONTRACT))
        self.handler = VehicleCommandHandler(self.coordinator, device_id="central-pi-01", ca_file=self.ca,
                                             public_key=self.public, publish_status=self.rejections.append)

    def power_off(self):
        self.central_app.stop.set()
        self.cluster_stop.set()
        self.cluster_runner.join(5)
        self.central_app.thread.join(5)
        self.bus.close()
        self.can.ports.clear()

    def close(self):
        self.power_off()
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def _stable_version(self, role):
        return next(t["software_version"] for t in self.coordinator.state.stable_bundle["targets"] if t["ecu_id"] == role)

    # -- laptop side ---------------------------------------------------------
    def _release(self, role, version, behavior, tag):
        payload = self.tmp / f"payload-{role}-{tag}"
        binary = payload / "bin" / "application"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\necho {role} {version}\n")
        binary.chmod(0o755)
        if behavior is not None:
            (payload / "behavior.json").write_text(json.dumps(behavior))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return build_release(payload, self.tmp / f"release-{role}-{tag}", BundleRequest(
            device_id={"central-control": "central-pi-01", "digital-cluster": "cluster-pi-02"}[role],
            version=version, entrypoint="bin/application", artifact_base_url=self.base,
            private_key_path=self.signing, job_id=str(uuid.uuid4()), created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1)))

    def publish_update(self, *, cluster_behavior=None, tag="update", tamper=None):
        """Run the real publisher CLI and return the MQTT command bytes."""
        versions = {"central-control": "1.1.0", "digital-cluster": "1.1.1"}
        releases = {"central-control": self._release("central-control", "1.1.0", None, tag),
                    "digital-cluster": self._release("digital-cluster", "1.1.1", cluster_behavior, tag)}
        for release in releases.values():
            archive = Path(release.manifest_path).with_name(Path(release.manifest.artifact_url).name)
            (self.release_root / archive.name).write_bytes(archive.read_bytes())
        if tamper is not None:
            target = self.release_root / Path(releases[tamper].manifest.artifact_url).name
            content = bytearray(target.read_bytes())
            content[len(content) // 2] ^= 0xFF  # Same size, different SHA-256.
            target.write_bytes(bytes(content))
        self.transaction_id = str(uuid.uuid4())
        spec = self.tmp / f"spec-{tag}.json"
        spec.write_text(json.dumps(_bundle_dict(self.transaction_id, versions, datetime.now(timezone.utc),
                                                self.base, {r: rel.manifest for r, rel in releases.items()})))
        args = ["vehicle-package", "--spec", str(spec), "--output", str(self.release_root),
                "--private-key", str(self.signing), "--public-key", str(self.public), "--base-url", self.base]
        for role, prefix in (("central-control", "central"), ("digital-cluster", "cluster")):
            args += [f"--{prefix}-manifest", str(releases[role].manifest_path),
                     f"--{prefix}-signature", str(releases[role].signature_path)]
        assert publisher_main(args) == 0
        sent = []

        class Mqtt:
            def publish_command(self, device, command):
                sent.append(command)

        assert publisher_main(["vehicle-publish", "--broker", "laptop", "--ca", "ca", "--cert", "c", "--key", "k",
            "--device-id", "central-pi-01", "--manifest", str(self.release_root / "2.0.0.vehicle-manifest.json"),
            "--signature", str(self.release_root / "2.0.0.vehicle-manifest.sig"), "--base-url", self.base],
            mqtt_factory=lambda **kwargs: Mqtt()) == 0
        return json.dumps(sent[0]).encode()

    # -- observations --------------------------------------------------------
    def slots(self):
        return {role: (installer.state.stable_slot, installer.state.active_slot, installer.state.stable_version)
                for role, installer in (("central-control", self.central_installer),
                                        ("digital-cluster", self.cluster_installer))}
