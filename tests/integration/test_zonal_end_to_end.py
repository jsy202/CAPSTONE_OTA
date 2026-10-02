"""Whole-vehicle scenarios over real TLS, real A/B slots and the real Cluster agent."""
import pytest

from capstone_ota.common.can_protocol import FunctionalTestRequestFrame, Gear
from tests.integration.zonal_harness import Vehicle


@pytest.fixture
def vehicle(tmp_path):
    vehicle = Vehicle(tmp_path)
    yield vehicle
    vehicle.close()


def _codes(result):
    return {error["code"] for item in result.evidence if item.get("check") == "verification"
            for error in item.get("errors", [])}


def test_signed_bundle_commits_both_applications_together(vehicle):
    vehicle.handler(vehicle.publish_update())
    state = vehicle.coordinator.state
    assert state.phase == "COMMITTED", state.last_error
    assert vehicle.slots() == {"central-control": ("B", "B", "1.1.0"), "digital-cluster": ("B", "B", "1.1.1")}
    assert [e["phase"] for e in vehicle.events] == ["PREPARING", "READY", "ACTIVATING", "VERIFYING", "COMMITTED"]
    assert vehicle.maintenance == [True, False]
    # Cluster fetched content only through Central's private transaction cache.
    token = vehicle.transaction_id[:8]
    assert (vehicle.cache_root / "transactions" / token / "cluster" / "transaction.json").is_file()
    functional = [e for e in state.evidence if e.get("check") == "verification"][0]["evidence"]
    assert any(item["check"] == "functional" and item["passed"] for item in functional)


def test_runtime_semantic_defect_rolls_back_whole_bundle(vehicle):
    # Declares the expected protocol but displays speed at 1/10 scale.
    vehicle.handler(vehicle.publish_update(cluster_behavior={"speed_divisor": 10}))
    state = vehicle.coordinator.state
    assert state.phase == "ROLLED_BACK", state.last_error
    assert state.last_error["code"] == "FUNCTIONAL_VALUE_MISMATCH"
    assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
    scopes = [item["scope"] for item in state.evidence if item.get("check") == "verification"]
    assert scopes == ["trial", "recovery"]
    assert [item["passed"] for item in state.evidence if item.get("check") == "verification"] == [False, True]
    assert vehicle.maintenance == [True, False]
    observed = vehicle.cluster_app.observe_functional_test(FunctionalTestRequestFrame(650, 3000, Gear.DRIVE, 0, 1))
    assert observed.speed == 650


def test_tampered_archive_aborts_before_either_slot_changes(vehicle):
    vehicle.handler(vehicle.publish_update(tamper="digital-cluster"))
    state = vehicle.coordinator.state
    assert state.phase == "ABORTED"
    assert state.last_error["code"] in {"DOWNLOAD_SIZE_MISMATCH", "ARTIFACT_HASH_MISMATCH"}
    assert vehicle.slots() == {"central-control": ("A", "A", None), "digital-cluster": ("A", "A", None)}
    assert vehicle.cluster_installer.state.completed_transactions == {}
    assert vehicle.maintenance == []


def test_cluster_heartbeat_loss_after_activation_rolls_back(vehicle):
    original = vehicle.agent.publish_heartbeat
    def lose_trial_heartbeats():
        if vehicle.cluster_installer.state.phase == "trial":
            return None
        return original()
    vehicle.agent.publish_heartbeat = lose_trial_heartbeats
    vehicle.handler(vehicle.publish_update())
    state = vehicle.coordinator.state
    assert state.phase == "ROLLED_BACK", state.last_error
    assert state.last_error["code"].startswith("HEARTBEAT_") or state.last_error["code"] == "PROCESS_UNHEALTHY"
    assert vehicle.slots()["digital-cluster"] == ("A", "A", None)


def test_unsigned_command_is_rejected_without_journal_change(vehicle):
    raw = vehicle.publish_update()
    (vehicle.release_root / "2.0.0.vehicle-manifest.sig").write_bytes(b"\0" * 64)
    vehicle.handler(raw)
    assert vehicle.rejections[-1]["last_error"]["code"] == "SIGNATURE_INVALID"
    assert vehicle.coordinator.state.phase == "IDLE"
