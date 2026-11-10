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

from . import drive_cycle


def play(rows, send, clock=time.monotonic, sleep=time.sleep, loops: int = 1, hz: int = 10,
         should_stop=None, start_counter: int = 0) -> int:
    """Send `loops` passes of `rows` (0 = until should_stop). send(message, actual_time)."""
    start, sent = clock(), 0
    passes = itertools.count() if loops == 0 else range(loops)
    for _ in passes:
        for message in drive_cycle.frames(rows, start_counter=(start_counter + sent) % 256):
            if should_stop and should_stop():
                return sent
            target = start + sent / hz
            while (remaining := target - clock()) > 0:
                sleep(remaining)
            send(message, clock())
            sent += 1
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
    args = parser.parse_args(argv)
    samples = (drive_cycle.load_speed_samples(args.cycle_file, args.unit) if args.cycle_file
               else drive_cycle.SYNTHETIC_CYCLE)
    rows = drive_cycle.build_profile(samples, args.hz)
    stop = {'now': False}
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.update(now=True))
    send, close = can_sender(args.channel)
    try:
        count = play(rows, send, loops=args.loops, hz=args.hz, should_stop=lambda: stop['now'])
    finally:
        close()
    print(json.dumps({'sent': count, 'rows_per_loop': len(rows), 'hz': args.hz}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
