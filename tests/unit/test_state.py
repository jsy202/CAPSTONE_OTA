import json

import pytest

from capstone_ota.agent.state import OtaState
from capstone_ota.common.errors import OtaError


def test_missing_state_returns_safe_defaults(tmp_path):
    state = OtaState.load(tmp_path / "missing.json")
    assert state.current_version is None
    assert state.previous_version is None
    assert state.completed_jobs == {}
    assert state.failed_versions == []
    assert state.active_job is None


def test_state_round_trips_and_bounds_completed_job_history(tmp_path):
    path = tmp_path / "state.json"
    state = OtaState(
        current_version="1.2.3",
        previous_version="1.2.2",
        completed_jobs={f"job-{index}": {"stage": "success"} for index in range(120)},
        failed_versions=["1.1.0"],
        active_job=None,
    )

    state.save_atomic(path)
    loaded = OtaState.load(path)

    assert loaded.current_version == "1.2.3"
    assert loaded.previous_version == "1.2.2"
    assert len(loaded.completed_jobs) == 100
    assert list(loaded.completed_jobs)[0] == "job-20"


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2},
        {"schema_version": 1, "current_version": 3},
        {"schema_version": 1, "unknown": True},
        ["not", "an", "object"],
    ],
)
def test_malformed_state_fails_closed(tmp_path, document):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(document))
    with pytest.raises(OtaError) as error:
        OtaState.load(path)
    assert error.value.code == "STATE_INVALID"


def test_failed_atomic_replace_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    previous = OtaState(current_version="1.0.0")
    previous.save_atomic(path)
    replacement = OtaState(current_version="1.0.1")

    def fail_replace(_source, _target):
        raise OSError("simulated power loss")

    monkeypatch.setattr("capstone_ota.agent.state.os.replace", fail_replace)
    with pytest.raises(OSError):
        replacement.save_atomic(path)

    assert OtaState.load(path).current_version == "1.0.0"
