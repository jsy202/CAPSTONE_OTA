"""bench.monitor_baseline / profile-rate wiring (no board needed)."""
import struct

import pytest

from bench import incident_field as field
from bench import monitor_baseline
from capstone_ota.monitor import trace


def _vehicle(counter, speed=500):
    raw = struct.pack('>HHBBB', speed, 1500, 3, 0, counter)
    return raw + bytes([trace.crc8(raw + trace.VEHICLE_DATA_ID)])


def _dump(hz, seconds, delay):
    lines, t = [], 1000.0
    for i in range(int(hz * seconds)):
        counter = i % 256
        lines.append(f"({t:.6f}) can0 200#{_vehicle(counter).hex()}")
        echo = trace.encode_echo(500, counter)
        lines.append(f"({t + delay:.6f}) can0 300#{echo.hex()}")
        t += 1.0 / hz
    return "\n".join(lines)


def test_baseline_is_fitted_from_the_recorded_latency_and_rate():
    baseline = monitor_baseline.baseline_from_candump(_dump(hz=50, seconds=5, delay=0.004))
    assert baseline["lat_mean"] == pytest.approx(0.004, abs=1e-4)
    assert baseline["skip_ratio"] == 0.0
    assert baseline["max_gap"] == pytest.approx(0.02, abs=1e-3)   # one 50 Hz period, not the 10 Hz 0.1 s


def test_an_empty_recording_is_refused_not_turned_into_a_baseline():
    with pytest.raises(ValueError, match="no vehicle/echo pairs"):
        monitor_baseline.baseline_from_candump("")


def test_the_monitor_uses_the_baseline_measured_at_its_own_rate():
    assert field.monitor_baseline_for(10) == field.MONITOR_BASELINE
    assert field.monitor_baseline_for(50).endswith("baseline-50hz.json")


def test_the_demo_cycle_gets_its_own_baseline_file_and_the_old_one_stays():
    assert field.monitor_baseline_for(50, "demo").endswith("baseline-50hz-demo.json")
    assert field.monitor_baseline_for(50, "synthetic").endswith("baseline-50hz.json")
    assert field.monitor_baseline_for(10, "demo") != field.MONITOR_BASELINE   # 10 Hz file was not measured on it
