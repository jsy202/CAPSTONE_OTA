"""Play a synthetic drive profile onto SocketCAN as vehicle frames (CAN 0x200) at a fixed rate.

Frames are scheduled on an absolute time grid, so a slow send never accumulates drift. The counter
continues across loops. Test data only: the profile is synthetic, not a real vehicle.
"""
from __future__ import annotations

import argparse
import itertools
import json
import signal
import socket
import struct
import sys
import time
from pathlib import Path

from capstone_ota.qt_source import frame

from . import drive_cycle


def file_hold(path):
    """A hold that is on while `path` exists (the orchestrator touches/removes it on Pi1)."""
    path = Path(path)
    return lambda: path.exists()


BRAKE_KMH_S = 9.0      # a held car brakes like a car ...
DRIVE_OFF_KMH_S = 6.0  # ... and drives off like one (0 to 80 km/h in about 13 s)


def play(rows, send, clock=time.monotonic, sleep=time.sleep, loops: int = 1, hz: int = 10,
         should_stop=None, start_counter: int = 0, hold=None, shifts=drive_cycle.UP_SHIFT_KMH) -> int:
    """Send `loops` passes of `rows` (0 = until should_stop). send(message, actual_time).

    While `hold()` is true the vehicle stands still: it brakes at BRAKE_KMH_S to 0 (neutral, idle), the profile does
    not advance and the counter keeps running, so the bus stays alive. When the hold ends the car drives off at
    DRIVE_OFF_KMH_S until it meets the profile again, which went on exactly where it stopped. (A rollback or an install
    keeps the cluster off for a minute; without this the car would be at 80 km/h when it is back, or jump there.)"""
    start, sent = clock(), 0
    passes = itertools.count() if loops == 0 else range(loops)
    shown, gear = None, 0            # the speed on the bus (tenths of km/h) once it differs from the profile's

    def emit(message):
        nonlocal sent
        while (remaining := start + sent / hz - clock()) > 0:
            sleep(remaining)
        send(message, clock())
        sent += 1

    def slewed(speed):
        nonlocal gear
        gear = drive_cycle.select_gear(speed / 10, gear, shifts)
        return frame(int(round(speed)), (start_counter + sent) % 256, rpm=drive_cycle.engine_rpm(speed / 10, gear), gear=gear)

    for _ in passes:
        for row in rows:
            while hold is not None and hold():
                if should_stop and should_stop():
                    return sent
                shown = max(0.0, (row.speed if shown is None else shown) - BRAKE_KMH_S * 10 / hz)
                emit(slewed(shown))
            if should_stop and should_stop():
                return sent
            if shown is not None:                       # after a hold: climb, then merge into the profile
                shown = min(row.speed, shown + DRIVE_OFF_KMH_S * 10 / hz)
                if shown >= row.speed:
                    shown = None
                else:
                    emit(slewed(shown))
                    continue
            emit(frame(row.speed, (start_counter + sent) % 256, rpm=row.rpm, gear=row.gear))
            gear = row.gear
    return sent


def can_sender(channel: str):
    bus = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    bus.bind((channel,))
    bus.settimeout(1)
    frame = struct.Struct('=IB3x8s')

    def send(message, _at):
        bus.send(frame.pack(message['id'], 8, bytes.fromhex(message['data'])))
    return send, bus.close


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='can0')
    parser.add_argument('--cycle-file', help='speed-time file; default is the built-in synthetic cycle')
    parser.add_argument('--unit', default='kmh', choices=['kmh', 'mph'])
    parser.add_argument('--loops', type=int, default=0, help='0 = until stopped')
    parser.add_argument('--hz', type=int, default=10)
    parser.add_argument('--cycle', default='synthetic', choices=['synthetic', 'demo'])
    parser.add_argument('--hold-file', type=Path, help='while this file exists the vehicle stands still (speed 0) and the profile waits')
    args = parser.parse_args(argv)
    samples = (drive_cycle.load_speed_samples(args.cycle_file, args.unit) if args.cycle_file
               else drive_cycle.named_cycle(args.cycle))
    rows = drive_cycle.build_profile(samples, args.hz, shifts=drive_cycle.DEMO_SHIFT_KMH if args.cycle == 'demo' else drive_cycle.UP_SHIFT_KMH)
    stop = {'now': False}
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.update(now=True))
    send, close = can_sender(args.channel)
    try:
        count = play(rows, send, loops=args.loops, hz=args.hz, should_stop=lambda: stop['now'],
                     hold=file_hold(args.hold_file) if args.hold_file else None,
                     shifts=drive_cycle.DEMO_SHIFT_KMH if args.cycle == 'demo' else drive_cycle.UP_SHIFT_KMH)
    finally:
        close()
    print(json.dumps({'sent': count, 'rows_per_loop': len(rows), 'hz': args.hz}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
