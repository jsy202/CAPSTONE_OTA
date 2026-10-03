"""Software/system integration verification (SWE.6): UI status over the real
zonal OTA path - real TLS servers, real A/B slots on both ECUs, the real
Cluster zone agent and coordinator on a virtual CAN bus.

The Cluster's systemd interactions are emulated exactly where they happen in
production: every application restart first runs the status snapshot
(digital-cluster.service ExecStartPre), and every durable coordinator event
also runs one watch iteration (capstone-ota-ui-status.service). The display
state is whatever capstone_ota.agent.ui_status publishes.
"""
import stat

import pytest

from capstone_ota.agent.ui_status import read_previous, run_once, watch
from tests.integration.zonal_harness import Vehicle
from tests.verification_evidence import record


class DisplayProbe:
    """Hooks the UI status publisher into a running Vehicle."""

    def __init__(self, vehicle, out):
        self.vehicle, self.out, self.timeline = vehicle, out, []
        self.root = vehicle.tmp / "cluster"

    def attach(self):
        installer = self.vehicle.cluster_installer
        services = installer.service_manager
        probe = self

        class RestartWithSnapshot:
            def restart_and_wait_healthy(self, unit, timeout_seconds):
                run_once(probe.root, "1.0.0", probe.out)            # ExecStartPre
                probe._observe("app-restart")
                return services.restart_and_wait_healthy(unit, timeout_seconds)

        installer.service_manager = RestartWithSnapshot()
        progress = self.vehicle.coordinator.progress

        def progress_and_watch(event):
            progress(event)
            watch(probe.root, "1.0.0", probe.out, 1.0, iterations=1, sleep=lambda _: None)
            probe._observe(event["phase"])

        self.vehicle.coordinator.progress = progress_and_watch
        run_once(self.root, "1.0.0", self.out)
        self._observe("boot")
        return self

    def _observe(self, source):
        status = read_previous(self.out)
        self.timeline.append({"source": source, "state": status["state"], "version": status["version"],
                              "restored": status["restored_at"] is not None})

    def shown(self):
        """De-duplicated (state, version) sequence a viewer would see."""
        sequence = []
        for item in self.timeline:
            pair = (item["state"], item["version"])
            if not sequence or sequence[-1] != pair:
                sequence.append(pair)
        return sequence


@pytest.fixture
def vehicle(tmp_path):
    vehicle = Vehicle(tmp_path)
    yield vehicle
    vehicle.close()


@pytest.fixture
def display(vehicle, tmp_path):
    (tmp_path / "run").mkdir()
    return DisplayProbe(vehicle, tmp_path / "run" / "digital-cluster.json").attach()


def _verification(state):
    return [item for item in state.evidence if item.get("check") == "verification"]


def _codes(state):
    return sorted({e["code"] for item in _verification(state) for e in item.get("errors", [])})


def test_normal_update_display_sequence_stable_trial_stable_new(vehicle, display):
    vehicle.handler(vehicle.publish_update())
    state = vehicle.coordinator.state
    assert state.phase == "COMMITTED", state.last_error
    assert display.shown() == [("stable", "1.0.0"), ("trial", "1.1.1"), ("stable", "1.1.1")]
    assert not any(item["restored"] for item in display.timeline)
    assert vehicle.slots()["digital-cluster"] == ("B", "B", "1.1.1")
    record("VR-UI-003", {"scenario": "normal update", "coordinator_phases": [e["phase"] for e in vehicle.events],
                         "display_sequence": display.shown(), "timeline": display.timeline,
                         "final_slots": vehicle.slots()})


def test_runtime_defect_display_sequence_and_recovery_verification(vehicle, display):
    vehicle.handler(vehicle.publish_update(cluster_behavior={"speed_divisor": 10}))
    state = vehicle.coordinator.state
    assert state.phase == "ROLLED_BACK", state.last_error
    assert state.last_error["code"] == "FUNCTIONAL_VALUE_MISMATCH"
    assert display.shown() == [("stable", "1.0.0"), ("trial", "1.1.1"), ("stable", "1.0.0")]
    assert display.timeline[-1]["restored"] is True
    verification = _verification(state)
    assert [v["scope"] for v in verification] == ["trial", "recovery"]
    assert [v["passed"] for v in verification] == [False, True]
    assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
    record("VR-OTA-003", {"scenario": "runtime semantic defect", "injected_fault": {"speed_divisor": 10},
                          "detected": state.last_error["code"], "verification_codes": _codes(state),
                          "coordinator_phases": [e["phase"] for e in vehicle.events],
                          "trial_slots": {"central-control": "B / 1.1.0", "digital-cluster": "B / 1.1.1"},
                          "final_slots": vehicle.slots(), "display_sequence": display.shown(),
                          "recovery_verification_passed": verification[-1]["passed"]})


def test_power_loss_during_verification_display_sequence(tmp_path):
    class PowerLoss(BaseException):
        pass

    (tmp_path / "run").mkdir()
    out = tmp_path / "run" / "digital-cluster.json"
    vehicle = Vehicle(tmp_path)
    try:
        display = DisplayProbe(vehicle, out).attach()

        def cut_power(*args, **kwargs):
            raise PowerLoss()
        vehicle.coordinator.probe.collect = cut_power
        with pytest.raises(PowerLoss):
            vehicle.handler(vehicle.publish_update())
        assert vehicle.coordinator.state.phase == "VERIFYING"
        assert display.shown()[-1] == ("trial", "1.1.1")
        before_power_loss = display.shown()
        vehicle.power_off()
        out.unlink()  # /run is a tmpfs: the status file does not survive a reboot

        vehicle.boot()
        display = DisplayProbe(vehicle, out).attach()
        result = vehicle.coordinator.recover_on_startup()
        assert result.phase == "ROLLED_BACK", result.last_error
        assert result.last_error["code"] == "UPDATE_INTERRUPTED"
        assert display.shown()[-1] == ("stable", "1.0.0")
        assert display.timeline[-1]["restored"] is False  # documented limit: no banner after reboot
        recovery = [v for v in _verification(vehicle.coordinator.state) if v.get("scope") == "recovery"]
        assert recovery and recovery[-1]["passed"]
        record("VR-OTA-005", {"scenario": "power loss during VERIFYING", "before_power_loss": before_power_loss,
                              "after_boot": display.shown(), "final_phase": result.phase,
                              "last_error": result.last_error["code"],
                              "recovery_verification_passed": recovery[-1]["passed"], "final_slots": vehicle.slots()})
    finally:
        vehicle.close()


def test_trial_display_version_matches_signed_bundle_target(vehicle, display):
    vehicle.handler(vehicle.publish_update())
    bundle = vehicle.coordinator.state.current_bundle
    signed = next(t["software_version"] for t in bundle["targets"] if t["ecu_id"] == "digital-cluster")
    shown_trial = [item["version"] for item in display.timeline if item["state"] == "trial"]
    assert shown_trial and set(shown_trial) == {signed}
    record("VR-OTA-002", {"signed_bundle_cluster_version": signed, "displayed_trial_versions": sorted(set(shown_trial))})


def test_ui_status_never_exposes_privileged_journal(vehicle, display):
    vehicle.handler(vehicle.publish_update(cluster_behavior={"speed_divisor": 10}))
    journal = vehicle.tmp / "cluster" / "state.json"
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    published = display.out.read_text()
    status = read_previous(display.out)
    assert set(status) == {"schema_version", "state", "version", "restored_at"}
    assert vehicle.transaction_id not in published
    assert '"trial_digest"' not in published and "completed_transactions" not in published
    assert stat.S_IMODE(display.out.stat().st_mode) == 0o644
    record("VR-UI-006", {"journal_mode": oct(stat.S_IMODE(journal.stat().st_mode)),
                         "status_mode": oct(stat.S_IMODE(display.out.stat().st_mode)),
                         "status_keys": sorted(status), "transaction_id_leaked": False})
