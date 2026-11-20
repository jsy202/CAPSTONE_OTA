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


def play(rows, send, clock=time.monotonic, sleep=time.sleep, loops: int = 1, hz: int = 10,
         should_stop=None, start_counter: int = 0, hold=None) -> int:
    """Send `loops` passes of `rows` (0 = until should_stop). send(message, actual_time).

    While `hold()` is true the vehicle stands still: speed 0 in neutral, the profile does not advance and the counter
    keeps running, so the bus stays alive. When the hold ends the profile goes on exactly where it stopped. (During a
    rollback the cluster is off for a minute; without this the car would have reached 80 km/h by the time it is back.)"""
    start, sent = clock(), 0
    passes = itertools.count() if loops == 0 else range(loops)

    def emit(message):
        nonlocal sent
        while (remaining := start + sent / hz - clock()) > 0:
            sleep(remaining)
        send(message, clock())
        sent += 1

    for _ in passes:
        for row in rows:
            while hold is not None and hold():
                if should_stop and should_stop():
                    return sent
                emit(frame(0, (start_counter + sent) % 256, rpm=drive_cycle.IDLE_RPM, gear=0))
            if should_stop and should_stop():
                return sent
            emit(frame(row.speed, (start_counter + sent) % 256, rpm=row.rpm, gear=row.gear))
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
                     hold=file_hold(args.hold_file) if args.hold_file else None)
    finally:
        close()
    print(json.dumps({'sent': count, 'rows_per_loop': len(rows), 'hz': args.hz}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
