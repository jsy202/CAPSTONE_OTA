"""Live field monitor (runs on the Verifier board): passive CAN receive -> windowed verdicts.

`Monitor` is a pure state machine fed with timestamped frames, so it is tested against recorded and
simulated traces. `main()` wires it to SocketCAN (kernel receive timestamps), writes a verdict JSON
every second, saves a freeze frame when suspicion or confirmation first appears, and accepts a
reset request file so each probation starts from a clean slate. The monitor never transmits.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from . import detect, trace
from .metrics import Baseline, quantile

SCM_TIMESTAMPNS = 35
FRAME = struct.Struct('=IB3x8s')


def decode_message(raw: bytes, ancdata, iface: str):
    """Build a CanFrame from a raw SocketCAN read plus its kernel timestamp; None if untrustworthy."""
    if len(raw) != FRAME.size:
        return None
    identifier, size, data = FRAME.unpack(raw)
    if identifier > 0x7FF or size != 8:
        return None
    for level, kind, payload in ancdata:
        if level == socket.SOL_SOCKET and kind == SCM_TIMESTAMPNS and len(payload) == 16:
            seconds, nanoseconds = struct.unpack('ll', payload)
            return trace.CanFrame(seconds + nanoseconds / 1e9, iface, identifier, data)
    return None


class Monitor:
    def __init__(self, baseline: Baseline, thresholds: detect.Thresholds | None = None, grace: float = 2.0,
                 blind_after: float = 3.0, buffer_seconds: float = 900.0, persist: int = 3, min_votes: int = 2):
        self.baseline, self.th = baseline, thresholds or detect.Thresholds()
        self.grace, self.blind_after, self.buffer_seconds = grace, blind_after, buffer_seconds
        self.persist, self.min_votes = persist, min_votes
        self.seq = 0
        self.reset(0.0)

    def reset(self, now: float, nonce: str | None = None) -> None:
        self.anchor, self.next_t0 = now, now
        self.frames, self.assessments, self.events = deque(), [], []
        self.confirmation, self.state = None, 'observing'
        self.reset_id = uuid4().hex[:12]
        self.reset_nonce = nonce
        self.monitored, self.last_tick, self.last_vehicle = 0.0, None, None
        self.zero_since, self.bus_speed, self.frames_seen = None, 0, 0

    def feed(self, frame: trace.CanFrame) -> None:
        if frame.t < self.anchor:
            return
        self.frames.append(frame)
        self.frames_seen += 1
        if frame.id == trace.VEHICLE_ID:
            try:
                vehicle = trace.decode_vehicle(frame.data)
            except ValueError:
                return
            self.last_vehicle, self.bus_speed = frame.t, vehicle.speed
            if vehicle.speed == 0:
                self.zero_since = self.zero_since if self.zero_since is not None else frame.t
            else:
                self.zero_since = None
        while self.frames and frame.t - self.frames[0].t > self.buffer_seconds:
            self.frames.popleft()

    def freeze_frame(self, now: float, seconds: float = 60.0) -> list:
        return [f for f in self.frames if now - seconds <= f.t <= now]

    def _evaluate(self, now: float) -> None:
        while self.next_t0 + self.th.window + self.grace <= now:
            end = self.next_t0 + self.th.window + self.grace
            subset = [f for f in self.frames if self.next_t0 <= f.t <= end]
            self.assessments.append(detect.assess_window(trace.pair_frames(subset), self.baseline, self.th, self.next_t0))
            self.next_t0 += self.th.step
        if self.confirmation is None and self.assessments:
            self.confirmation = detect.confirm(self.assessments, self.persist, self.min_votes)

    def _recent_delay(self, now: float):
        """p99 processing delay over the last ~5 s (display value; decisions use the 10 s windows)."""
        subset = [f for f in self.frames if now - 6.0 <= f.t <= now]
        latencies = [p.latency for p in trace.pair_frames(subset).pairs if p.t_source >= now - 5.5]
        return round(quantile(latencies, 0.99) * 1000, 2) if len(latencies) >= 5 else None

    def tick(self, now: float) -> dict:
        self.seq += 1
        elapsed = 0.0 if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        reference = self.last_vehicle if self.last_vehicle is not None else self.anchor
        blind = now - reference > self.blind_after
        if not blind:
            self.monitored += elapsed
        self._evaluate(now)
        last = self.assessments[-1] if self.assessments else None
        suspicious = last is not None and last.votes >= 1 and last.cls not in ('normal', 'insufficient')
        state = ('confirmed' if self.confirmation else 'blind' if blind else 'suspect' if suspicious else 'observing')
        if state != self.state:
            self.events.append((now, state))
            self.state = state
        shown = self.confirmation or last
        return {'seq': self.seq, 'now': now, 'state': state, 'reset_id': self.reset_id, 'reset_nonce': self.reset_nonce,
                'cls': shown.cls if shown and shown.cls != 'insufficient' else 'normal',
                'fired': list(shown.fired) if shown else [],
                'confirm_t': self.confirmation.t if self.confirmation else None,
                'monitored_s': round(self.monitored, 3), 'windows': len(self.assessments),
                'recent_delay_ms': self._recent_delay(now), 'latency_limit_ms': round(self.th.latency_limit * 1000, 1),
                'frames': self.frames_seen, 'bus_speed': self.bus_speed,
                'bus_stopped_s': round(now - self.zero_since, 3) if self.zero_since is not None and not blind else 0.0,
                'last_window': None if last is None else {
                    't0': last.t0, 'cls': last.cls, 'votes': last.votes, 'lat_p99_ms': None if last.metrics.lat_p99 is None
                    else round(last.metrics.lat_p99 * 1000, 2), 'skip_ratio': round(last.metrics.skip_ratio, 3)}}


def atomic_json(path: Path, data) -> None:
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)


def open_socket(channel: str) -> socket.socket:
    bus = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    mask = 0x7FF
    bus.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack('=IIII', 0x200, mask, 0x300, mask))
    bus.setsockopt(socket.SOL_SOCKET, SCM_TIMESTAMPNS, 1)   # SO_TIMESTAMPNS shares the cmsg type number
    bus.settimeout(0.2)
    bus.bind((channel,))
    return bus


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', default='can0')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--once', action='store_true', help='exit after one verdict write (smoke test)')
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / 'evidence').mkdir(exist_ok=True)
    monitor = Monitor(Baseline(**json.loads(args.baseline.read_text())))
    monitor.reset(time.time())
    bus, verdict_path, reset_path = open_socket(args.channel), args.out_dir / 'verdict.json', args.out_dir / 'monitor.reset'
    seen_events, next_tick = 0, time.time() + 1
    while True:
        try:
            raw, anc, _flags, _addr = bus.recvmsg(FRAME.size, 256)
            frame = decode_message(raw, anc, args.channel)
            if frame is not None:
                monitor.feed(frame)
        except socket.timeout:
            pass
        now = time.time()
        if now < next_tick:
            continue
        next_tick = now + 1
        if reset_path.exists():
            nonce = reset_path.read_text().strip()[:64] or None
            reset_path.unlink(missing_ok=True)
            monitor.reset(now, nonce=nonce)
            seen_events = 0
        verdict = monitor.tick(now)
        atomic_json(verdict_path, verdict)
        for moment, state in monitor.events[seen_events:]:
            if state in ('suspect', 'confirmed'):
                name = args.out_dir / 'evidence' / f'freeze-{int(moment)}-{state}.log'
                name.write_text('\n'.join(trace.format_candump_line(f) for f in monitor.freeze_frame(moment)) + '\n')
        seen_events = len(monitor.events)
        if args.once:
            return 0


if __name__ == '__main__':
    raise SystemExit(main())
