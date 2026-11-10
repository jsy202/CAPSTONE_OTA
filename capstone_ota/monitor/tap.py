"""Fast, local delay readout for the cockpit display.

Reads the bus on the board that shows it (receive-only, kernel timestamps) and reports the processing
delay of the last ~2 s. It is for display only: it shares the board with the thing being watched, so the
independent decision stays with the Verifier board's verdict.
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque

from . import trace
from .metrics import quantile
from .service import FRAME, decode_message, open_socket


class SpeedTracker:
    """Latest vehicle speed from this board's own bus view, with staleness (a silent bus is never 'stopped')."""

    def __init__(self, max_age: float = 1.5):
        self.max_age, self.speed, self.at = max_age, None, None

    def feed(self, frame: trace.CanFrame) -> None:
        if frame.id != trace.VEHICLE_ID:
            return
        try:
            self.speed, self.at = trace.decode_vehicle(frame.data).speed / 10.0, frame.t
        except ValueError:
            pass

    def snapshot(self, now: float) -> dict:
        fresh = self.at is not None and 0 <= now - self.at <= self.max_age
        return {'model_speed': self.speed if fresh else None, 'valid': fresh}


class DelayTap:
    def __init__(self, window: float = 2.0):
        self.window, self.frames = window, deque()

    def feed(self, frame: trace.CanFrame) -> None:
        self.frames.append(frame)
        while self.frames and frame.t - self.frames[0].t > self.window + 3.0:
            self.frames.popleft()

    def snapshot(self, now: float) -> dict:
        subset = [f for f in self.frames if now - self.window - 1.5 <= f.t <= now]
        result = trace.pair_frames(subset)
        pairs = [p for p in result.pairs if p.t_source >= now - self.window]
        latest_source = latest_echo = None
        for f in reversed(self.frames):                      # newest decodable frame of each kind
            try:
                if latest_source is None and f.id == trace.VEHICLE_ID:
                    latest_source = (f, trace.decode_vehicle(f.data))
                elif latest_echo is None and f.id == trace.ECHO_ID:
                    latest_echo = (f, trace.decode_echo(f.data))
            except ValueError:
                continue
            if latest_source and latest_echo:
                break
        can = None if latest_source is None or now - latest_source[0].t > 2.0 else {
            'speed': latest_source[1].speed / 10.0, 'rpm': latest_source[1].rpm, 'gear': latest_source[1].gear}
        cluster = None
        if latest_echo is not None and latest_source is not None and now - latest_echo[0].t <= 4.0:
            cluster = {'speed': latest_echo[1].speed / 10.0,
                       'behind_frames': (latest_source[1].counter - latest_echo[1].counter) % 256}
        # Frames that arrived but have no echo yet are at least as delayed as they have been waiting (a lower bound).
        waiting = [now - s.t for s in result.unmatched_sources if now - 3.0 <= s.t <= now]
        pending_ms = max(waiting) * 1000 if waiting else 0.0
        latencies = [p.latency for p in pairs]
        if len(latencies) < 3 and pending_ms == 0.0:
            return {'delay_ms': None, 'max_ms': None, 'n': len(pairs), 'can': can, 'cluster': cluster}
        p99 = quantile(latencies, 0.99) * 1000 if len(latencies) >= 3 else 0.0
        return {'delay_ms': round(max(p99, pending_ms), 1), 'max_ms': round(max([x * 1000 for x in latencies] + [pending_ms]), 1),
                'n': len(pairs), 'can': can, 'cluster': cluster}


def run(channel: str, tap, running) -> None:
    """Blocking receive loop (use in a daemon thread). `running` is a callable returning False to stop."""
    bus = open_socket(channel)
    while running():
        try:
            raw, anc, _flags, _addr = bus.recvmsg(FRAME.size, 256)
        except socket.timeout:
            continue
        frame = decode_message(raw, anc, channel)
        if frame is not None:
            tap.feed(frame)
