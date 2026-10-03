"""Unit verification (A1) of the Central Control application core."""
import importlib.util
import json
from pathlib import Path

import pytest

from capstone_ota.common.can_protocol import ApplicationState, Gear, HeartbeatFrame, VehicleStatusFrame
from capstone_ota.coordinator.compatibility import CanContract

ROOT = Path(__file__).parents[2]


def load_central():
    spec = importlib.util.spec_from_file_location(
        "central_control", ROOT / "apps" / "central-control" / "lib" / "central_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_central()


class Transport:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(frame)

    def ids(self, can_id):
        return [f for f in self.frames if f.can_id == can_id]


def identity_file(tmp_path, state="stable", version="1.0.0"):
    path = tmp_path / "central-control.json"
    path.write_text(json.dumps({"schema_version": 1, "state": state, "version": version, "restored_at": None}))
    return path


def app(tmp_path, **kwargs):
    transport = Transport()
    central = C.CentralControl(transport, identity_file(tmp_path, **kwargs), start=0.0)
    return central, transport


def run(central, until, step=0.01, start=0.0):
    t = start
    while t < until - 1e-9:
        central.tick(t)
        t = round(t + step, 6)


# --- deterministic vehicle profile ------------------------------------------

@pytest.mark.parametrize("t, expected", [
    (0.0, (0, 800, Gear.PARK, 0)), (2.99, (0, 800, Gear.PARK, 0)),
    (3.0, (0, 1500, Gear.DRIVE, 0)), (10.0, (800, 2500, Gear.DRIVE, 0)),
    (14.99, (800, 2500, Gear.DRIVE, 0)), (15.0, (800, 2500, Gear.DRIVE, 0)),
    (20.0, (0, 800, Gear.PARK, 0)), (40.5, (0, 800, Gear.PARK, 0)),
])
def test_profile_phase_boundaries(t, expected):
    assert C.profile(t) == expected


def test_profile_is_deterministic_and_inside_contract():
    contract = CanContract()
    for k in range(0, 2000):
        t = k * 0.01
        speed, rpm, gear, warnings = C.profile(t)
        assert C.profile(t) == (speed, rpm, gear, warnings)
        assert 0 <= speed <= contract.max_speed and 0 <= rpm <= contract.max_rpm
        assert warnings & ~contract.allowed_warning_mask == 0 and isinstance(gear, Gear)
        VehicleStatusFrame(speed, rpm, gear, warnings, 0).encode()


def test_maintenance_profile_is_stationary():
    assert C.MAINTENANCE_PROFILE == (0, 800, Gear.PARK, 0)


# --- runtime identity from the sanitized status file ------------------------

@pytest.mark.parametrize("state, version, expected", [
    ("stable", "1.0.0", (ApplicationState.STABLE, (1, 0, 0))),
    ("trial", "1.1.0", (ApplicationState.TRIAL, (1, 1, 0))),
    ("unknown", None, None), ("stable", "1.0", None), ("stable", "1.0.256", None),
    ("stable", "1.0.0-rc1", None), ("trial", "a.b.c", None),
])
def test_identity_partitions(tmp_path, state, version, expected):
    assert C.read_identity(identity_file(tmp_path, state, version)) == expected


def test_missing_or_corrupt_identity_is_none(tmp_path):
    assert C.read_identity(tmp_path / "absent.json") is None
    (tmp_path / "bad.json").write_text("{corrupt")
    assert C.read_identity(tmp_path / "bad.json") is None


# --- scheduler with a fake clock ---------------------------------------------

def test_exact_frame_counts_over_ten_seconds(tmp_path):
    central, transport = app(tmp_path)
    run(central, 10.0)
    assert len(transport.ids(0x100)) == 10
    assert len(transport.ids(0x200)) == 100


def test_heartbeat_carries_identity_and_protocol(tmp_path):
    central, transport = app(tmp_path, state="trial", version="1.1.0")
    central.tick(0.0)
    heartbeat = HeartbeatFrame.decode(transport.ids(0x100)[0])
    assert (heartbeat.ecu_id, heartbeat.software_version, heartbeat.state) == ("central-control", (1, 1, 0), ApplicationState.TRIAL)
    assert (heartbeat.protocol_major, heartbeat.protocol_minor) == (1, 0)


def test_identity_is_reread_every_heartbeat(tmp_path):
    central, transport = app(tmp_path, state="trial", version="1.1.0")
    central.tick(0.0)
    identity_file(tmp_path, "stable", "1.1.0")  # commit while running
    central.tick(1.0)
    states = [HeartbeatFrame.decode(f).state for f in transport.ids(0x100)]
    assert states == [ApplicationState.TRIAL, ApplicationState.STABLE]


def test_counters_are_contiguous_through_rollover(tmp_path):
    central, transport = app(tmp_path)
    run(central, 30.0, step=0.05)  # 300 vehicle frames, 30 heartbeats
    vehicle = [VehicleStatusFrame.decode(f).counter for f in transport.ids(0x200)]
    assert vehicle[:3] == [0, 1, 2] and 255 in vehicle
    assert all((b - a) % 256 == 1 for a, b in zip(vehicle, vehicle[1:]))
    beats = [HeartbeatFrame.decode(f).counter for f in transport.ids(0x100)]
    assert all((b - a) % 256 == 1 for a, b in zip(beats, beats[1:]))


def test_no_burst_after_stall(tmp_path):
    central, transport = app(tmp_path)
    run(central, 1.0)
    before = (len(transport.ids(0x100)), len(transport.ids(0x200)))
    central.tick(6.0)  # 5 s stall
    after = (len(transport.ids(0x100)), len(transport.ids(0x200)))
    assert (after[0] - before[0], after[1] - before[1]) == (1, 1)
    assert central.tick(6.01) == pytest.approx(6.1)


def test_unknown_identity_suppresses_heartbeat_but_not_vehicle(tmp_path):
    central, transport = app(tmp_path, state="unknown", version=None)
    run(central, 3.0)
    assert transport.ids(0x100) == [] and len(transport.ids(0x200)) == 30


def test_maintenance_switches_vehicle_to_stationary_and_keeps_heartbeat(tmp_path):
    central, transport = app(tmp_path)
    run(central, 5.0)  # accelerating phase
    central.set_maintenance(True)
    count = len(transport.frames)
    run(central, 7.0, start=5.0)
    new = transport.frames[count:]
    vehicle = [VehicleStatusFrame.decode(f) for f in new if f.can_id == 0x200]
    assert {(v.speed, v.rpm, v.gear, v.warnings) for v in vehicle} == {(0, 800, Gear.PARK, 0)}
    assert len([f for f in new if f.can_id == 0x100]) == 2
    assert central.maintenance is True
    central.set_maintenance(False)
    assert central.maintenance is False


def test_periods_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        C.CentralControl(Transport(), identity_file(tmp_path), heartbeat_period=0, start=0.0)
