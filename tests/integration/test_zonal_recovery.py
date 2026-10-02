"""Power interruption while both trial applications are active."""
import pytest

from tests.integration.zonal_harness import Vehicle


class PowerLoss(BaseException):
    """Not an Exception: the coordinator must not get to handle it."""


def test_restart_during_verification_restores_both_stable_slots(tmp_path):
    vehicle = Vehicle(tmp_path)
    try:
        def cut_power(*args, **kwargs):
            raise PowerLoss()
        vehicle.coordinator.probe.collect = cut_power
        with pytest.raises(PowerLoss):
            vehicle.handler(vehicle.publish_update())
        assert vehicle.coordinator.state.phase == "VERIFYING"
        assert vehicle.central_installer.state.phase == "trial"
        assert vehicle.cluster_installer.state.phase == "trial"
        vehicle.power_off()

        vehicle.boot()
        result = vehicle.coordinator.recover_on_startup()
        assert result.phase == "ROLLED_BACK", result.last_error
        assert result.last_error["code"] == "UPDATE_INTERRUPTED"
        assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
        recovery = [e for e in vehicle.coordinator.state.evidence
                    if e.get("check") == "verification" and e.get("scope") == "recovery"]
        assert recovery and recovery[-1]["passed"]
        assert vehicle.maintenance[-1] is False
    finally:
        vehicle.close()
