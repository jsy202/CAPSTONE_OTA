import json
import os
from dataclasses import asdict, replace

import pytest

from capstone_ota.common.errors import OtaError


TX = "550e8400-e29b-41d4-a716-446655440000"


def api():
    from capstone_ota.coordinator.state import VehicleTransactionState
    return VehicleTransactionState


def test_exact_state_graph_and_illegal_transitions():
    State = api()
    graph = {
        "IDLE": {"PREPARING"}, "PREPARING": {"READY", "ABORTED"},
        "READY": {"ACTIVATING", "ABORTED"}, "ACTIVATING": {"VERIFYING", "ROLLING_BACK"},
        "VERIFYING": {"COMMITTED", "ROLLING_BACK"},
        "ROLLING_BACK": {"RECOVERY_VERIFYING"},
        "RECOVERY_VERIFYING": {"ROLLED_BACK", "RECOVERY_FAILED"},
        "COMMITTED": set(), "ABORTED": set(), "ROLLED_BACK": set(), "RECOVERY_FAILED": set(),
    }
    for source, allowed in graph.items():
        for destination in graph:
            state = replace(State(), phase=source)
            if destination in allowed:
                assert state.transition(destination).phase == destination
            else:
                with pytest.raises(OtaError) as error:
                    state.transition(destination)
                assert error.value.code == "TRANSACTION_TRANSITION_INVALID"


def test_durable_token_history_blocks_collision_even_after_completion(tmp_path):
    State = api()
    state = State().retain_transaction(TX, "a" * 64)
    path = tmp_path / "state.json"
    state = replace(state, completed_transactions={TX: "ABORTED"})
    state.save_atomic(path)
    loaded = State.load(path)
    assert loaded.retain_transaction(TX, "a" * 64) == loaded
    with pytest.raises(OtaError, match="TRANSACTION_TOKEN_COLLISION"):
        loaded.retain_transaction("550e8400-1111-4111-a111-111111111111", "b" * 64)
    with pytest.raises(OtaError, match="TRANSACTION_CONFLICT"):
        loaded.retain_transaction(TX, "b" * 64)


def test_atomic_write_fsyncs_file_before_replace_and_parent_after(tmp_path, monkeypatch):
    State = api()
    path = tmp_path / "state.json"
    calls = []
    real_sync, real_replace = os.fsync, os.replace

    def sync(fd):
        calls.append("directory" if os.path.isdir(f"/proc/self/fd/{fd}") else "file")
        real_sync(fd)

    def replace_file(source, destination):
        calls.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", replace_file)
    State().save_atomic(path)
    assert calls == ["file", "replace", "directory"]
    assert State.load(path) == State()
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("change", [
    {"phase": "FUTURE"}, {"schema_version": True}, {"schema_version": 2},
    {"extra": 1}, {"attempts": -1}, {"attempts": True},
    {"token_history": {"550e8400": "11111111-e29b-41d4-a716-446655440000"}},
    {"completed_transactions": {TX: "READY"}}, {"last_error": "oops"},
])
def test_corrupt_unknown_or_inconsistent_state_rejected(tmp_path, change):
    State = api()
    data = dict(asdict(State()), **change)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data))
    with pytest.raises(OtaError) as error:
        State.load(path)
    assert error.value.code == "TRANSACTION_STATE_INVALID"


@pytest.mark.parametrize("raw", ["{", "null", '{"phase":"IDLE","phase":"READY"}'])
def test_malformed_or_duplicate_state_rejected(tmp_path, raw):
    path = tmp_path / "state.json"
    path.write_text(raw)
    with pytest.raises(OtaError, match="TRANSACTION_STATE_INVALID"):
        api().load(path)


@pytest.mark.parametrize("mutation", ["bundle_digest", "ecu_role", "slot", "phase", "missing_ecu", "baseline"])
def test_tampered_active_metadata_cannot_be_loaded(tmp_path, mutation):
    from tests.unit.test_coordinator import Harness
    h = Harness(tmp_path)
    h.execute()
    data = json.loads(h.state_path.read_text())
    if mutation == "bundle_digest":
        data["current_bundle"]["bundle_version"] = "9.0.0"
        data["bundle_version"] = "9.0.0"
    elif mutation == "ecu_role":
        data["ecu_states"]["future-ecu"] = data["ecu_states"]["central-control"]
    elif mutation == "slot":
        data["ecu_states"]["central-control"]["active_slot"] = "Z"
    elif mutation == "phase":
        data["ecu_states"]["central-control"]["phase"] = "FUTURE"
    elif mutation == "missing_ecu":
        del data["ecu_states"]["digital-cluster"]
    else:
        data["stable_bundle"] = None
    h.state_path.write_text(json.dumps(data))
    with pytest.raises(OtaError, match="TRANSACTION_STATE_INVALID"):
        api().load(h.state_path)
