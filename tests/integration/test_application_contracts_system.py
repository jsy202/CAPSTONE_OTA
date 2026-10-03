"""Software/system integration (A4-A7): the zonal OTA end to end with the real
application-contract implementations instead of the harness doubles.

* Central: the real Central Control app (apps/central-control) on the virtual
  CAN bus, its identity produced by the real capstone-ota-ui-status helper at
  every restart (ExecStartPre emulation), maintenance through its real IPC.
* Cluster: the real patched Qt dashboard (offscreen) when CAPSTONE_CLUSTER_APP
  is set; every Cluster slot restart relaunches active-slot/bin/application,
  so a rollback really restores the previous binary. Without it, the harness
  Cluster double is used and only the Central contract is exercised.

Real coordinator, real zone agent, real TLS servers and real A/B slots as in
the existing zonal harness; only systemd and the CAN wire are emulated.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tests.integration.zonal_harness as H
from capstone_ota.agent.ui_status import read_previous, run_once
from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.publisher.bundle import BundleRequest, build_release
from tests.unit.test_central_control import C
from tests.verification_evidence import record


class RealCentral:
    """Drop-in for zonal_harness.CentralApp running the real application."""
    sabotage_trial_identity = False

    def __init__(self, port, installer):
        self.root = installer.install_root
        run_dir = self.root.parent / "run"
        run_dir.mkdir(exist_ok=True)
        self.identity = run_dir / "central-control.json"
        self.socket = run_dir / "central.sock"
        self._snapshot()
        services = installer.service_manager
        central = self

        class RestartProcess:   # central-control.service restart: ExecStartPre, then a new process
            def restart_and_wait_healthy(self, unit, timeout_seconds):
                central.stop.set()
                central.thread.join(5)
                central._snapshot()
                central._spawn()
                central.thread.start()
                central.restarts += 1
                return services.restart_and_wait_healthy(unit, timeout_seconds)

        installer.service_manager = RestartProcess()
        self.port, self.state_dir, self.restarts = port, run_dir / "central-state", 0
        self.state_dir.mkdir(exist_ok=True)
        self._spawn()

    def _spawn(self):
        """A fresh application object, as a restarted process would be: nothing survives in memory."""
        self.app = C.CentralControl(self.port, self.identity, heartbeat_period=H.CONTRACT.heartbeat_period_s,
                                    vehicle_period=H.CONTRACT.vehicle_period_s, state_dir=self.state_dir)
        self.server = C.MaintenanceServer(self.socket, self.app)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _snapshot(self):
        status = run_once(self.root, "1.0.0", self.identity)
        if self.sabotage_trial_identity and status["state"] == "trial":
            self.identity.write_text(json.dumps({**status, "state": "unknown", "version": None}))

    def _run(self):
        try:
            C.run(self.app, self.server, self.stop)
        finally:
            self.server.close()


class QtCluster:
    """Runs active-slot/bin/application of the Cluster install root."""

    def __init__(self, root: Path, tmp: Path):
        self.root, self.tmp = root, tmp
        self.socket = tmp / "run" / "cluster.sock"
        self.status = tmp / "run" / "digital-cluster.json"
        self.process = None
        self.launched = []

    def restart(self):
        self.terminate()
        run_once(self.root, "1.0.0", self.status)        # badge snapshot, as in PR #1
        binary = self.root / "active-slot" / "bin" / "application"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CAPSTONE_CLUSTER_IPC_SOCKET=str(self.socket),
                   CAPSTONE_APP_STATUS_FILE=str(self.status),
                   LD_LIBRARY_PATH=str(Path(os.environ["CAPSTONE_QT_DIR"]) / "lib"))
        log = (self.tmp / f"cluster-{len(self.launched)}.log").open("w")
        self.process = subprocess.Popen([str(binary)], env=env, stdout=log, stderr=subprocess.STDOUT)
        self.launched.append({"slot": os.readlink(self.root / "active-slot"),
                              "badge": read_previous(self.status), "log": Path(log.name).name})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.connect(str(self.socket))
                return True
            except OSError:
                time.sleep(0.1)
        return False

    def terminate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class AppVehicle(H.Vehicle):
    """Cluster releases carry the real normal or fault-injection binary."""
    cluster_binaries: dict = {}

    def _release(self, role, version, behavior, tag):
        build = (behavior or {}).get("build")
        if role != "digital-cluster" or build is None:
            return super()._release(role, version, behavior, tag)
        payload = self.tmp / f"payload-{role}-{tag}"
        (payload / "bin").mkdir(parents=True)
        shutil.copy2(self.cluster_binaries[build], payload / "bin" / "application")
        if build == "fault":
            (payload / "FAULT-INJECTION-ONLY").write_text("DEMO/TEST FAULT INJECTION ONLY\n")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return build_release(payload, self.tmp / f"release-{role}-{tag}", BundleRequest(
            device_id="cluster-pi-02", version=version, entrypoint="bin/application", artifact_base_url=self.base,
            private_key_path=self.signing, job_id=str(uuid.uuid4()), created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1)))


def _qt_binaries():
    qt, normal, fault = (os.environ.get(k) for k in ("CAPSTONE_QT_DIR", "CAPSTONE_CLUSTER_APP", "CAPSTONE_CLUSTER_FAULT_APP"))
    if not (qt and normal and fault):
        pytest.skip("CAPSTONE_QT_DIR / CAPSTONE_CLUSTER_APP / CAPSTONE_CLUSTER_FAULT_APP not set")
    return {"normal": Path(normal), "fault": Path(fault)}


def _wire_maintenance(vehicle):
    client = ApplicationIpc(vehicle.tmp / "run" / "central.sock", timeout_s=2.0)

    def maintenance(enabled, timeout_s):
        client.set_maintenance(enabled, timeout_s=timeout_s)
        vehicle.maintenance.append(enabled)
    vehicle.coordinator.maintenance = maintenance


def _attach_qt(vehicle, binaries):
    cluster = QtCluster(vehicle.tmp / "cluster", vehicle.tmp)
    services = vehicle.cluster_installer.service_manager

    class RestartQt:              # digital-cluster.service emulation
        def restart_and_wait_healthy(self, unit, timeout_seconds):
            return cluster.restart() and services.restart_and_wait_healthy(unit, timeout_seconds)

    vehicle.cluster_installer.service_manager = RestartQt()
    assert cluster.restart(), "factory Cluster app did not start"
    return cluster


@pytest.fixture
def real_central(monkeypatch):
    monkeypatch.setattr(H, "CentralApp", RealCentral)
    RealCentral.sabotage_trial_identity = False
    yield
    RealCentral.sabotage_trial_identity = False


@pytest.fixture
def real_cluster(monkeypatch, tmp_path):
    binaries = _qt_binaries()
    slot_a = tmp_path / "cluster" / "slots" / "A" / "bin"
    slot_a.mkdir(parents=True)
    shutil.copy2(binaries["normal"], slot_a / "application")   # factory 1.0.0 build
    monkeypatch.setattr(H, "ClusterApp", lambda installer: ApplicationIpc(tmp_path / "run" / "cluster.sock",
                                                                          timeout_s=2.0))
    monkeypatch.setattr(AppVehicle, "cluster_binaries", binaries)
    return binaries


def _verification(state):
    return [item for item in state.evidence if item.get("check") == "verification"]


def _heartbeat_evidence(state, scope):
    item = next(v for v in _verification(state) if v["scope"] == scope)
    return [e for e in item["evidence"] if e.get("check") == "heartbeat" and e.get("ecu_id") == "central-control"]


def _functional(state, scope):
    item = next(v for v in _verification(state) if v["scope"] == scope)
    return [e for e in item["evidence"] if e.get("check") == "functional"]


def _watch_maintenance_at(vehicle, phase):
    seen = []
    progress = vehicle.coordinator.progress

    def hook(event):
        progress(event)
        if event["phase"] == phase:
            seen.append((vehicle.central_app.restarts, vehicle.central_app.app.maintenance))
    vehicle.coordinator.progress = hook
    return seen


def test_real_central_contract_commits_with_harness_cluster(real_central, tmp_path):
    vehicle = AppVehicle(tmp_path)
    try:
        _wire_maintenance(vehicle)
        at_verifying = _watch_maintenance_at(vehicle, "VERIFYING")
        vehicle.handler(vehicle.publish_update())
        state = vehicle.coordinator.state
        assert state.phase == "COMMITTED", state.last_error
        # Central was really restarted into the trial slot and kept maintenance on.
        assert at_verifying == [(1, True)]
        heartbeat = _heartbeat_evidence(state, "trial")
        assert heartbeat and heartbeat[0]["samples"] > 0 and heartbeat[0]["expected_version"] == "1.1.0"
        assert vehicle.maintenance == [True, False]
        assert vehicle.slots()["central-control"] == ("B", "B", "1.1.0")
    finally:
        vehicle.close()


def test_central_identity_loss_during_trial_rolls_back_and_recovers(real_central, tmp_path):
    RealCentral.sabotage_trial_identity = True
    vehicle = AppVehicle(tmp_path)
    try:
        vehicle.handler(vehicle.publish_update())
        state = vehicle.coordinator.state
        assert state.phase == "ROLLED_BACK", state.last_error
        codes = {e["code"] for v in _verification(state) for e in v.get("errors", [])}
        assert any(code.startswith("HEARTBEAT_") for code in codes), codes
        assert [v["passed"] for v in _verification(state)] == [False, True]
        assert vehicle.slots()["central-control"] == ("A", "A", None)
    finally:
        vehicle.close()


def test_real_apps_normal_update_commits(real_central, real_cluster, tmp_path):
    vehicle = AppVehicle(tmp_path)
    cluster = _attach_qt(vehicle, real_cluster)
    try:
        _wire_maintenance(vehicle)
        vehicle.handler(vehicle.publish_update(cluster_behavior={"build": "normal"}))
        state = vehicle.coordinator.state
        assert state.phase == "COMMITTED", state.last_error
        functional = _functional(state, "trial")
        assert functional and all(f["passed"] for f in functional)
        assert [l["slot"] for l in cluster.launched] == ["slots/A", "slots/B"]
        assert [l["badge"]["state"] for l in cluster.launched] == ["stable", "trial"]
        assert vehicle.slots() == {"central-control": ("B", "B", "1.1.0"), "digital-cluster": ("B", "B", "1.1.1")}
        record("VR-APP-011", {"scenario": "normal update with real applications", "final_phase": state.phase,
                              "functional": functional, "cluster_launches": cluster.launched,
                              "maintenance": vehicle.maintenance, "final_slots": vehicle.slots()})
    finally:
        cluster.terminate()
        vehicle.close()


def test_real_fault_build_is_detected_and_whole_vehicle_rolls_back(real_central, real_cluster, tmp_path):
    vehicle = AppVehicle(tmp_path)
    cluster = _attach_qt(vehicle, real_cluster)
    try:
        _wire_maintenance(vehicle)
        vehicle.handler(vehicle.publish_update(cluster_behavior={"build": "fault"}))
        state = vehicle.coordinator.state
        assert state.phase == "ROLLED_BACK", state.last_error
        assert state.last_error["code"] == "FUNCTIONAL_VALUE_MISMATCH"
        trial = _functional(state, "trial")
        mismatch = next(f for f in trial if not f["passed"])
        assert mismatch["expected"][0] == 650 and mismatch["observed"][0] == 65
        recovery = _functional(state, "recovery")
        assert recovery and all(f["passed"] for f in recovery)
        assert [v["passed"] for v in _verification(state)] == [False, True]
        assert [l["slot"] for l in cluster.launched] == ["slots/A", "slots/B", "slots/A"]
        badge = [l["badge"] for l in cluster.launched]
        assert [(b["state"], b["version"]) for b in badge] == [("stable", "1.0.0"), ("trial", "1.1.1"), ("stable", "1.0.0")]
        assert badge[-1]["restored_at"] is not None
        assert "DEMO/TEST FAULT INJECTION BUILD" in (cluster.tmp / cluster.launched[1]["log"]).read_text()
        assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
        assert vehicle.maintenance == [True, False]
        record("VR-APP-010", {"scenario": "fault-injection Cluster build in trial", "injected_fault":
                              "CAPSTONE_FAULT_SPEED_DIVISOR=10 (compile-time, display path)",
                              "detected": state.last_error["code"], "mismatch": mismatch,
                              "recovery_functional": recovery, "cluster_launches": cluster.launched,
                              "final_slots": vehicle.slots(), "recovery_verification_passed": True})
    finally:
        cluster.terminate()
        vehicle.close()


def test_real_apps_power_loss_during_verification_recovers(real_central, real_cluster, tmp_path):
    class PowerLoss(BaseException):
        pass

    vehicle = AppVehicle(tmp_path)
    cluster = _attach_qt(vehicle, real_cluster)
    try:
        def cut_power(*args, **kwargs):
            raise PowerLoss()
        vehicle.coordinator.probe.collect = cut_power
        with pytest.raises(PowerLoss):
            vehicle.handler(vehicle.publish_update(cluster_behavior={"build": "normal"}))
        assert vehicle.coordinator.state.phase == "VERIFYING"
        cluster.terminate()
        vehicle.power_off()
        (tmp_path / "run" / "digital-cluster.json").unlink(missing_ok=True)   # /run is tmpfs

        vehicle.boot()
        cluster = _attach_qt(vehicle, real_cluster)       # boots the trial slot B
        _wire_maintenance(vehicle)
        result = vehicle.coordinator.recover_on_startup()
        assert result.phase == "ROLLED_BACK", result.last_error
        recovery = [v for v in _verification(vehicle.coordinator.state) if v.get("scope") == "recovery"]
        assert recovery and recovery[-1]["passed"]
        assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
        assert cluster.launched[-1]["slot"] == "slots/A"
        record("VR-APP-013", {"scenario": "power loss during verification with real applications",
                              "final_phase": result.phase, "recovery_verification_passed": recovery[-1]["passed"],
                              "cluster_launches_after_boot": cluster.launched, "final_slots": vehicle.slots()})
    finally:
        cluster.terminate()
        vehicle.close()
