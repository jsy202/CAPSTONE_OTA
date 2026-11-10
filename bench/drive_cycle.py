"""Synthetic drive profile derived from a speed-time cycle.

Only vehicle speed comes from the cycle file; gear and rpm are derived with a simple, deterministic
model (not any real vehicle). The output is test data for the bench, never real vehicle data.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterator, NamedTuple

from capstone_ota.qt_source import frame

KMH_PER_MPH = 1.609344
UP_SHIFT_KMH = (15.0, 28.0, 42.0, 58.0, 78.0)   # speed at which gear n shifts up to n+1
DOWN_FACTOR = 0.85                              # downshift at 85% of the upshift speed (hysteresis)
GEAR_RATIOS = {1: 3.6, 2: 2.2, 3: 1.5, 4: 1.1, 5: 0.85, 6: 0.7}
FINAL_DRIVE = 3.9
TIRE_RADIUS_M = 0.32
IDLE_RPM = 800
MAX_RPM = 6500
MAX_ACCEL_KMH_S = 15.0
MAX_DECEL_KMH_S = 18.0

# Small hand-made cycle for development and tests: starts, cruises and stops, several gears.
SYNTHETIC_CYCLE = [
    (0.0, 0.0), (6.0, 0.0), (16.0, 50.0), (28.0, 50.0), (36.0, 0.0), (44.0, 0.0),
    (58.0, 80.0), (70.0, 80.0), (76.0, 60.0), (84.0, 60.0), (96.0, 0.0), (104.0, 0.0),
]


class Row(NamedTuple):
    t: float
    speed: int   # tenths of km/h, the unit used by the 0x200 frame
    rpm: int
    gear: int


def load_speed_samples(path, unit: str = 'kmh') -> list[tuple[float, float]]:
    """Read '<seconds> <speed>' lines (space or comma separated); non-numeric lines are skipped."""
    if unit not in ('kmh', 'mph'):
        raise ValueError('unit must be kmh or mph')
    factor = KMH_PER_MPH if unit == 'mph' else 1.0
    samples: list[tuple[float, float]] = []
    for line in Path(path).read_text().splitlines():
        parts = [p for p in re.split(r'[,\s]+', line.strip()) if p]
        if len(parts) < 2:
            continue
        try:
            moment, speed = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if speed < 0 or not math.isfinite(moment) or not math.isfinite(speed):
            raise ValueError('invalid speed sample')
        if samples and moment <= samples[-1][0]:
            raise ValueError('cycle time must strictly increase')
        samples.append((moment, speed * factor))
    if not samples:
        raise ValueError('no numeric speed samples found')
    return samples


def resample(samples, hz: int = 10) -> list[float]:
    """Linear interpolation of speed onto a fixed rate; endpoints are preserved exactly."""
    if hz < 1:
        raise ValueError('hz must be positive')
    start, end = samples[0][0], samples[-1][0]
    count = round((end - start) * hz) + 1
    speeds, index = [], 0
    for step in range(count):
        moment = start + step / hz
        while index < len(samples) - 2 and moment > samples[index + 1][0]:
            index += 1
        if len(samples) == 1:
            speeds.append(samples[0][1])
            continue
        (t0, v0), (t1, v1) = samples[index], samples[index + 1]
        ratio = min(max((moment - t0) / (t1 - t0), 0.0), 1.0)
        speeds.append(v0 + (v1 - v0) * ratio)
    speeds[-1] = samples[-1][1]
    return speeds


def limit_acceleration(speeds, hz: int = 10, accel: float = MAX_ACCEL_KMH_S,
                       decel: float = MAX_DECEL_KMH_S) -> list[float]:
    """Clamp per-tick speed change to physically plausible acceleration and braking."""
    limited = [float(speeds[0])]
    for target in speeds[1:]:
        previous = limited[-1]
        limited.append(max(0.0, min(previous + accel / hz, max(previous - decel / hz, target))))
    return limited


def select_gear(speed: float, current: int) -> int:
    """Neutral (0) at standstill, 1..6 by speed with hysteresis so the gear does not hunt."""
    if speed < (1.0 if current == 0 else 0.5):
        return 0
    gear = current or 1
    while gear < 6 and speed >= UP_SHIFT_KMH[gear - 1]:
        gear += 1
    while gear > 1 and speed < UP_SHIFT_KMH[gear - 2] * DOWN_FACTOR:
        gear -= 1
    return gear


def engine_rpm(speed: float, gear: int) -> int:
    if gear == 0:
        return IDLE_RPM
    wheel_rpm = speed / 3.6 / (2 * math.pi * TIRE_RADIUS_M) * 60
    return int(min(MAX_RPM, max(IDLE_RPM, wheel_rpm * GEAR_RATIOS[gear] * FINAL_DRIVE)))


def build_profile(samples, hz: int = 10) -> list[Row]:
    speeds = limit_acceleration(resample(samples, hz), hz)
    rows, gear = [], 0
    for tick, speed in enumerate(speeds):
        gear = select_gear(speed, gear)
        rows.append(Row(tick / hz, min(3000, int(round(speed * 10))), engine_rpm(speed, gear), gear))
    return rows


def stop_seconds(rows) -> float:
    """Total time spent at standstill (the window in which a safe rollback may open)."""
    if len(rows) < 2:
        return 0.0
    return sum(1 for r in rows if r.speed == 0) * (rows[1].t - rows[0].t)


def changing_fraction(rows) -> float:
    """Share of ticks where speed changes; steady segments hide latency, changing ones expose it."""
    if len(rows) < 2:
        return 0.0
    return sum(1 for a, b in zip(rows, rows[1:]) if a.speed != b.speed) / (len(rows) - 1)


def frames(rows, start_counter: int = 0) -> Iterator[dict]:
    for index, row in enumerate(rows):
        yield frame(row.speed, (start_counter + index) % 256, rpm=row.rpm, gear=row.gear)
