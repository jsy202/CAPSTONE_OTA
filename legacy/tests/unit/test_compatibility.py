from dataclasses import replace

import pytest

from capstone_ota.common.can_protocol import (
    ApplicationState, CanFrame, FunctionalTestRequestFrame, FunctionalTestResultFrame,
    Gear, HeartbeatFrame, VehicleStatusFrame,
)
from tests.unit.test_vehicle_bundle import bundle_data, parse, dependency_data


def api():
    from capstone_ota.coordinator.compatibility import (
        CompatibilityValidator, CompatibilitySnapshot, FrameObservation,
    )
    return CompatibilityValidator, CompatibilitySnapshot, FrameObservation


def snapshot(*, trial=True, speed=650):
    _, Snapshot, Observation = api()
    frames = []
    for ecu in ("central-control", "digital-cluster"):
        for timestamp, counter in ((10.0, 0), (11.0, 1)):
            frame = HeartbeatFrame(ecu, (1, 2, 3), 1, 2,
                ApplicationState.TRIAL if trial else ApplicationState.STABLE, counter).encode()
            frames.append(Observation(timestamp, frame.can_id, frame.data))
    for timestamp, counter in ((10.0 + index / 10, (246 + index) % 256) for index in range(11)):
        frame = VehicleStatusFrame(650, 1500, Gear.DRIVE, 0, counter).encode()
        frames.append(Observation(timestamp, frame.can_id, frame.data))
    request = FunctionalTestRequestFrame(650, 1500, Gear.DRIVE, 0, 9)
    result = FunctionalTestResultFrame(speed, 1500, Gear.DRIVE, 0, 9).encode()
    return Snapshot(10.0, 11.0, {"central-control": True, "digital-cluster": True},
                    tuple(frames), (request,), (Observation(11.0, result.can_id, result.data),))


def validate(value, *, trial=True, bundle=None, not_before=10.0):
    Validator, _, _ = api()
    return Validator().validate_runtime(bundle or parse(bundle_data()), value,
                                         trial=trial, not_before=not_before)


def test_real_can_contract_and_functional_observations_pass_and_are_json_ready():
    import json
    result = validate(snapshot())
    assert result.passed
    assert result.errors == ()
    payload = result.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert {item["check"] for item in payload["evidence"]} >= {"process", "heartbeat", "can", "functional"}


def test_static_dependencies_return_structured_failure():
    Validator, _, _ = api()
    data = bundle_data()
    data["dependencies"] = [dependency_data()]
    assert Validator().validate_static(parse(data)).passed
    data["targets"][0]["protocol_major"] = 2
    result = Validator().validate_static(parse(data))
    assert not result.passed
    assert result.errors[0]["code"] == "DEPENDENCY_UNSATISFIED"


@pytest.mark.parametrize("kind,code", [
    ("process", "PROCESS_UNHEALTHY"), ("version", "HEARTBEAT_VERSION_MISMATCH"),
    ("protocol", "HEARTBEAT_VERSION_MISMATCH"), ("state", "HEARTBEAT_STATE_MISMATCH"),
    ("missing", "HEARTBEAT_MISSING"), ("counter", "HEARTBEAT_COUNTER_STALE"),
    ("timeout", "HEARTBEAT_TIMEOUT"), ("missed", "HEARTBEAT_PERIOD_MISSED"),
    ("window", "OBSERVATION_WINDOW_INVALID"), ("old", "OBSERVATION_WINDOW_INVALID"),
    ("crc", "CAN_FRAME_INVALID"), ("can_counter", "CAN_COUNTER_STALE"),
    ("id", "CAN_ID_UNEXPECTED"), ("dlc", "CAN_FRAME_INVALID"),
    ("range", "CAN_VALUE_OUT_OF_RANGE"), ("can_missing", "CAN_OBSERVATION_MISSING"),
    ("can_period", "CAN_PERIOD_MISSED"), ("can_timeout", "CAN_TIMEOUT"),
    ("functional_missing", "FUNCTIONAL_RESULT_MISSING"),
    ("functional_wrong_id", "FUNCTIONAL_RESULT_UNEXPECTED"),
])
def test_verification_detects_independent_contract_failures(kind, code):
    _, _, Observation = api()
    value = snapshot()
    frames = list(value.frames)
    if kind == "process":
        value = replace(value, process_health={"central-control": False, "digital-cluster": True})
    elif kind in {"version", "protocol", "state", "counter"}:
        heartbeat = HeartbeatFrame.decode(CanFrame(frames[1].can_id, frames[1].data))
        if kind == "version":
            heartbeat = replace(heartbeat, software_version=(9, 0, 0))
        elif kind == "protocol":
            heartbeat = replace(heartbeat, protocol_major=2)
        elif kind == "state":
            heartbeat = replace(heartbeat, state=ApplicationState.STABLE)
        else:
            heartbeat = replace(heartbeat, counter=0)
        frames[1] = Observation(11.0, 0x100, heartbeat.encode().data)
    elif kind == "missing":
        frames = [f for f in frames if f.can_id != 0x101]
    elif kind == "timeout":
        value = replace(value, ended_at=14.0)
    elif kind == "missed":
        value = replace(value, ended_at=16.0)
        frames[1] = replace(frames[1], observed_at=16.0)
    elif kind == "window":
        value = replace(value, ended_at=9.0)
    elif kind == "old":
        value = replace(value, started_at=8.0)
    elif kind == "crc":
        frames[-1] = replace(frames[-1], data=frames[-1].data[:-1] + b"\x00")
    elif kind == "can_counter":
        frames[-1] = replace(frames[-1], data=VehicleStatusFrame(650, 1500, Gear.DRIVE, 0, 255).encode().data)
    elif kind == "id":
        frames[-1] = replace(frames[-1], can_id=0x333)
    elif kind == "dlc":
        frames[-1] = replace(frames[-1], data=b"short")
    elif kind == "range":
        frames[-1] = replace(frames[-1], data=VehicleStatusFrame(4000, 1500, Gear.DRIVE, 0, 0).encode().data)
    elif kind == "can_missing":
        frames = [f for f in frames if f.can_id != 0x200]
    elif kind == "can_period":
        frames = [f for f in frames if f.can_id != 0x200] + [frames[4],
            replace(frames[-1], data=VehicleStatusFrame(650, 1500, Gear.DRIVE, 0, 247).encode().data)]
    elif kind == "can_timeout":
        frames = [f for f in frames if f.can_id != 0x200 or f.observed_at <= 10.4]
    elif kind == "functional_missing":
        value = replace(value, functional_results=())
    elif kind == "functional_wrong_id":
        result = FunctionalTestResultFrame(650, 1500, Gear.DRIVE, 0, 10).encode()
        value = replace(value, functional_results=(Observation(11.0, result.can_id, result.data),))
    value = replace(value, frames=tuple(frames))
    result = validate(value)
    assert not result.passed
    assert code in {error["code"] for error in result.errors}


def test_matching_declared_protocol_does_not_mask_interpreted_value_defect():
    result = validate(snapshot(speed=65))
    assert not result.passed
    assert result.errors == ({"code": "FUNCTIONAL_VALUE_MISMATCH", "ecu_id": "digital-cluster",
        "message": "interpreted values differ", "test_id": 9,
        "expected": [650, 1500, 3, 0], "observed": [65, 1500, 3, 0]},)


def test_recovery_requires_stable_heartbeats():
    assert validate(snapshot(trial=False), trial=False).passed
    assert not validate(snapshot(), trial=False).passed


def test_no_functional_challenges_never_counts_as_verification():
    result = validate(replace(snapshot(), requests=(), functional_results=()))
    assert not result.passed
    assert "FUNCTIONAL_TEST_MISSING" in {e["code"] for e in result.errors}


def test_declared_pair_major_mismatch_rejected_even_without_dependency_rules():
    Validator, _, _ = api()
    data = bundle_data()
    data["targets"][1]["protocol_major"] = 2
    result = Validator().validate_static(parse(data))
    assert not result.passed
    assert result.errors[0]["code"] == "CAN_DECLARATION_MISMATCH"


@pytest.mark.parametrize("version", ["1.2.3-beta", "1.2.3+build", "256.0.0"])
def test_unrepresentable_heartbeat_versions_rejected_statically(version):
    Validator, _, _ = api()
    data = bundle_data()
    data["targets"][0]["software_version"] = version
    assert Validator().validate_static(parse(data)).errors[0]["code"] == "VERSION_NOT_WIRE_REPRESENTABLE"


def test_configured_can_error_budget_never_masks_functional_failure():
    data = bundle_data()
    data["health_policy"]["max_error_count"] = 1
    value = snapshot()
    value = replace(value, frames=(*value.frames, replace(value.frames[-1], can_id=0x333)))
    assert validate(value, bundle=parse(data)).passed
    bad = snapshot(speed=65)
    bad = replace(bad, frames=(*bad.frames, replace(bad.frames[-1], can_id=0x333)))
    assert not validate(bad, bundle=parse(data)).passed


def test_zero_duration_samples_cannot_prove_sustained_health():
    value = snapshot()
    value = replace(value, ended_at=10.01,
        frames=tuple(replace(f, observed_at=10.0) for f in value.frames),
        functional_results=tuple(replace(f, observed_at=10.0) for f in value.functional_results))
    assert not validate(value).passed


def test_malformed_observation_returns_structured_failure():
    value = replace(snapshot(), frames=(None,))
    result = validate(value)
    assert not result.passed
    assert "OBSERVATION_INVALID" in {e["code"] for e in result.errors}


@pytest.mark.parametrize("invalid", ["duplicates", "all-range", "range-breaks-sequence", "backwards-counter"])
def test_tolerated_invalid_traffic_never_advances_valid_samples_or_freshness(invalid):
    _, _, Observation = api()
    data = bundle_data()
    data["health_policy"]["max_error_count"] = 20
    value = snapshot()
    heartbeat_frames = tuple(f for f in value.frames if f.can_id != 0x200)
    vehicle = []
    for index in range(11):
        counter, speed = (246 + index) % 256, 650
        if invalid == "duplicates":
            counter = 246
        elif invalid == "all-range":
            speed = 4000
        elif invalid == "range-breaks-sequence" and index == 1:
            speed = 4000
        elif invalid == "backwards-counter" and index >= 2:
            counter = 244 + (index % 2)
        encoded = VehicleStatusFrame(speed, 1500, Gear.DRIVE, 0, counter).encode()
        vehicle.append(Observation(10.0 + index / 10, encoded.can_id, encoded.data))
    result = validate(replace(value, frames=(*heartbeat_frames, *vehicle)), bundle=parse(data))
    assert not result.passed
    can = next(item for item in result.evidence if item["check"] == "can")
    assert can["samples"] == {"duplicates": 1, "all-range": 0,
                              "range-breaks-sequence": 1, "backwards-counter": 2}[invalid]
    assert can["last_observed_at"] == {"duplicates": 10.0, "all-range": None,
                                       "range-breaks-sequence": 10.0, "backwards-counter": 10.1}[invalid]
    codes = {error["code"] for error in result.errors}
    assert "CAN_TIMEOUT" in codes
    if invalid != "backwards-counter":
        assert "CAN_OBSERVATION_MISSING" in codes


@pytest.mark.parametrize("invalid", ["version", "state"])
def test_wrong_heartbeat_identity_or_state_does_not_count_as_valid_evidence(invalid):
    _, _, Observation = api()
    value = snapshot()
    frames = list(value.frames)
    heartbeat = HeartbeatFrame.decode(CanFrame(frames[1].can_id, frames[1].data))
    if invalid == "version":
        heartbeat = replace(heartbeat, software_version=(9, 0, 0))
    else:
        heartbeat = replace(heartbeat, state=ApplicationState.STABLE)
    frames[1] = Observation(11.0, 0x100, heartbeat.encode().data)
    result = validate(replace(value, frames=tuple(frames)))
    assert not result.passed
    central = next(item for item in result.evidence if item["check"] == "heartbeat" and item["ecu_id"] == "central-control")
    assert central["samples"] == 1
    assert "HEARTBEAT_MISSING" in {error["code"] for error in result.errors}
