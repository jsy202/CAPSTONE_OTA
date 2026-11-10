"""Synthetic drive profile built from a standard speed-time cycle (not real vehicle data)."""
import pytest

from bench import drive_cycle as dc
from capstone_ota.qt_source import frame


def test_loader_skips_headers_and_converts_mph(tmp_path):
    path = tmp_path / 'cycle.txt'
    path.write_text('Cycle header\nseconds mph\n0 0.0\n1 3.0\n\n2 10.0\n')
    samples = dc.load_speed_samples(path, unit='mph')
    assert [t for t, _ in samples] == [0.0, 1.0, 2.0]
    assert samples[1][1] == pytest.approx(3.0 * 1.609344)


def test_loader_kmh_is_unchanged_and_rejects_empty(tmp_path):
    path = tmp_path / 'cycle.txt'
    path.write_text('0,0\n1,5.5\n')
    assert dc.load_speed_samples(path, unit='kmh')[1] == (1.0, 5.5)
    path.write_text('nothing numeric\n')
    with pytest.raises(ValueError):
        dc.load_speed_samples(path)


def test_loader_rejects_unknown_unit_and_non_increasing_time(tmp_path):
    path = tmp_path / 'cycle.txt'
    path.write_text('0 0\n0 1\n')
    with pytest.raises(ValueError):
        dc.load_speed_samples(path)
    path.write_text('0 0\n1 1\n')
    with pytest.raises(ValueError):
        dc.load_speed_samples(path, unit='knots')


def test_resample_length_endpoints_and_midpoint():
    speeds = dc.resample([(0.0, 0.0), (1.0, 10.0), (2.0, 10.0)], hz=10)
    assert len(speeds) == 21
    assert speeds[0] == 0.0 and speeds[-1] == 10.0
    assert speeds[5] == pytest.approx(5.0)
    assert speeds[10] == pytest.approx(10.0)


def test_limit_acceleration_clamps_steps_and_keeps_stops():
    limited = dc.limit_acceleration([0.0, 100.0, 100.0, 0.0], hz=10, accel=15.0, decel=20.0)
    assert limited[0] == 0.0
    assert limited[1] == pytest.approx(1.5)           # 15 km/h/s * 0.1 s
    assert all(b - a <= 1.5 + 1e-9 for a, b in zip(limited, limited[1:]))
    assert all(a - b <= 2.0 + 1e-9 for a, b in zip(limited, limited[1:]))
    assert dc.limit_acceleration([0.0, 0.0], hz=10) == [0.0, 0.0]


def test_gear_is_neutral_at_stop_and_rises_with_speed():
    assert dc.select_gear(0.0, 0) == 0
    assert dc.select_gear(2.0, 0) == 1
    gears = [dc.select_gear(s, 0) for s in (2, 20, 35, 50, 70, 100)]
    assert gears == [1, 2, 3, 4, 5, 6]
    assert max(dc.select_gear(s, 0) for s in range(0, 300)) == 6


def test_gear_hysteresis_prevents_hunting():
    threshold = dc.UP_SHIFT_KMH[2]
    gear, changes = 0, 0
    for i in range(200):
        speed = threshold + (0.5 if i % 2 else -0.5)
        new = dc.select_gear(speed, gear)
        changes += new != gear
        gear = new
    assert changes <= 4  # neutral->1->2->3 once, then it must hold


def test_rpm_bounds_and_upshift_drop():
    for speed in range(0, 250, 5):
        for gear in range(0, 7):
            assert dc.IDLE_RPM <= dc.engine_rpm(float(speed), gear) <= dc.MAX_RPM
    assert dc.engine_rpm(0.0, 0) == dc.IDLE_RPM
    threshold = dc.UP_SHIFT_KMH[1]
    assert dc.engine_rpm(threshold, 3) < dc.engine_rpm(threshold, 2)
    assert dc.engine_rpm(60.0, 4) > dc.engine_rpm(40.0, 4)


def test_build_profile_is_deterministic_and_frame_compatible():
    cycle = dc.SYNTHETIC_CYCLE
    first = dc.build_profile(cycle, hz=10)
    assert first == dc.build_profile(cycle, hz=10)
    for row in first:
        assert type(row.speed) is int and 0 <= row.speed <= 3000
        frame(row.speed, 0, rpm=row.rpm, gear=row.gear)  # raises on invalid ranges
    assert first[0].t == 0.0 and first[1].t == pytest.approx(0.1)
    assert max(r.speed for r in first) > 400  # reaches > 40 km/h
    assert all(abs(b.speed - a.speed) <= 20 for a, b in zip(first, first[1:]))  # <= 2 km/h per tick


def test_profile_has_changing_segments_and_stops():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE, hz=10)
    assert dc.stop_seconds(rows) >= 5.0
    assert dc.changing_fraction(rows) > 0.4


def test_frames_counter_wraps_and_increments():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE, hz=10)
    out = list(dc.frames(rows, start_counter=250))
    counters = [bytes.fromhex(m['data'])[6] for m in out]
    assert counters[:8] == [250, 251, 252, 253, 254, 255, 0, 1]
    assert len(out) == len(rows)
    assert all(m['id'] == 0x200 for m in out)
