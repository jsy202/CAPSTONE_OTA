"""Synthetic CAN traces (source 0x200 + cluster echo 0x300) for developing the offline detectors.

Fault models are simple statistical stand-ins, NOT measurements of the real Pi bench. Real traces
and thresholds come from Gate 0 / hardware runs.
"""
from __future__ import annotations

import random

from capstone_ota.monitor.trace import CanFrame, ECHO_ID, VEHICLE_ID, encode_echo
from capstone_ota.qt_source import frame

MODES = ('normal', 'delay', 'freeze', 'drift', 'input')


def simulate(rows, mode: str = 'normal', seed: int = 0, hz: int = 10, start: float = 40.0,
             end: float = 1e9, t0: float = 1000.0, base_delay: float = 0.012) -> list[CanFrame]:
    """mode: normal | delay (heavy-tail extra latency) | freeze (echo stalls) |
    drift (latency grows linearly) | input (jittery Central source, healthy cluster)."""
    if mode not in MODES:
        raise ValueError('unknown mode')
    rng = random.Random(seed)
    frames: list[CanFrame] = []
    stall_until, next_stall = -1.0, start
    for index, row in enumerate(rows):
        nominal = index / hz
        active = start <= nominal < end
        jitter = rng.uniform(-0.045, 0.045) if (mode == 'input' and active) else rng.gauss(0, 0.0005)
        sent = t0 + nominal + jitter
        counter = index % 256
        data = bytes.fromhex(frame(row.speed, counter, rpm=row.rpm, gear=row.gear)['data'])
        frames.append(CanFrame(sent, 'can0', VEHICLE_ID, data))
        delay = max(0.001, rng.gauss(base_delay, 0.002))
        if active and mode == 'delay':
            delay += 0.05 + rng.expovariate(1 / 0.06)
        elif active and mode == 'drift':
            delay += 0.002 * (nominal - start)
        elif active and mode == 'freeze':
            if nominal >= next_stall and nominal >= stall_until:
                stall_until = nominal + 0.1 * rng.randint(3, 6)
                next_stall = stall_until + rng.uniform(2.0, 4.0)
            if nominal < stall_until:
                continue                              # cluster produced no echo for this frame
        frames.append(CanFrame(sent + delay, 'can0', ECHO_ID, encode_echo(row.speed, counter)))
    return sorted(frames, key=lambda f: f.t)
