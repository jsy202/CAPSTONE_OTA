"""Pi3 live monitor core: stateful verdicts from a frame stream, driven by recorded/simulated traces."""
import json

import pytest

from bench import drive_cycle as dc
from bench.trace_sim import simulate
from capstone_ota.monitor import detect, metrics, trace
from capstone_ota.monitor.service import Monitor

T0 = 1000.0
ROWS = dc.build_profile(dc.SYNTHETIC_CYCLE)


@pytest.fixture(scope='module')
def baseline():
    return metrics.fit_baseline(trace.pair_frames(simulate(ROWS, mode='normal', seed=100, t0=T0)))


def drive(monitor, frames, until=None, step=1.0, start=None):
    """Feed frames in time order and tick once per `step` seconds of trace time; return verdicts."""
    frames = sorted(frames, key=lambda f: f.t)
    now = start if start is not None else frames[0].t
    end = until if until is not None else frames[-1].t + 5
    verdicts, index = [], 0
    while now <= end:
        while index < len(frames) and frames[index].t <= now:
            monitor.feed(frames[index])
            index += 1
        verdicts.append(monitor.tick(now))
        now += step
    return verdicts


def test_healthy_stream_is_observed_and_never_confirmed(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    verdicts = drive(monitor, simulate(ROWS, mode='normal', seed=1, t0=T0), until=T0 + 100)   # stream still running
    assert verdicts[-1]['state'] in ('observing', 'suspect')
    assert all(v['state'] != 'confirmed' for v in verdicts)
    assert verdicts[-1]['windows'] >= 15 and verdicts[-1]['monitored_s'] > 90


def test_delay_fault_is_confirmed_with_class_and_evidence(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    verdicts = drive(monitor, simulate(ROWS, mode='delay', seed=2, start=40.0, t0=T0))
    confirmed = [v for v in verdicts if v['state'] == 'confirmed']
    assert confirmed and confirmed[0]['cls'] == 'delay' and set(confirmed[0]['fired']) & {'latency_p99', 'cusum'}
    assert confirmed[0]['confirm_t'] - (T0 + 40.0) <= 30.0
    assert verdicts[-1]['state'] == 'confirmed'                     # latched until reset
    assert any(e[1] == 'suspect' for e in monitor.events) and monitor.events[-1][1] == 'confirmed'


def test_silent_central_makes_the_monitor_blind_and_stops_the_clock(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    frames = [f for f in simulate(ROWS, mode='normal', seed=3, t0=T0) if f.t < T0 + 20]
    verdicts = drive(monitor, frames, until=T0 + 40)
    assert verdicts[-1]['state'] == 'blind'
    before = [v for v in verdicts if v['state'] == 'blind'][0]['monitored_s']
    assert verdicts[-1]['monitored_s'] == pytest.approx(before, abs=1.5)    # blind time is not counted


def test_reset_clears_state_and_rotates_the_id(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    first_id = monitor.tick(T0)['reset_id']
    drive(monitor, simulate(ROWS, mode='delay', seed=4, start=20.0, t0=T0))
    assert monitor.tick(T0 + 120)['state'] == 'confirmed'
    monitor.reset(T0 + 121)
    fresh = monitor.tick(T0 + 121.5)
    assert fresh['state'] in ('observing', 'blind') and fresh['reset_id'] != first_id
    assert fresh['windows'] == 0 and fresh['monitored_s'] < 2


def test_bus_stopped_seconds_follow_the_vehicle_speed(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    verdicts = drive(monitor, simulate(ROWS, mode='normal', seed=5, t0=T0), until=T0 + 25)
    by_time = {round(v['now'] - T0): v for v in verdicts}
    assert by_time[5]['bus_stopped_s'] == pytest.approx(5, abs=1.5)         # cycle starts with 6 s standstill
    assert by_time[15]['bus_stopped_s'] == 0 and by_time[15]['bus_speed'] > 0


def test_verdict_is_json_with_stable_keys(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0)
    verdict = monitor.tick(T0 + 1)
    json.dumps(verdict)
    assert {'seq', 'now', 'state', 'cls', 'fired', 'confirm_t', 'monitored_s', 'bus_stopped_s', 'bus_speed',
            'windows', 'frames', 'reset_id'} <= set(verdict)
    assert monitor.tick(T0 + 2)['seq'] == verdict['seq'] + 1


def test_freeze_frame_returns_recent_frames(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    frames = simulate(ROWS, mode='normal', seed=6, t0=T0)
    drive(monitor, frames, until=T0 + 30)
    recent = monitor.freeze_frame(T0 + 30, seconds=10)
    assert recent and min(f.t for f in recent) >= T0 + 20 - 1e-6 and max(f.t for f in recent) <= T0 + 30 + 1e-6


def test_assess_window_matches_assess_trace(baseline):
    result = trace.pair_frames(simulate(ROWS, mode='delay', seed=7, start=40.0, t0=T0))
    th = detect.Thresholds()
    whole = detect.assess_trace(result, baseline, th)
    one = detect.assess_window(result, baseline, th, whole[3].t0)
    assert (one.fired, one.cls, one.votes) == (whole[3].fired, whole[3].cls, whole[3].votes)


def test_decode_message_uses_the_kernel_timestamp():
    import socket
    import struct
    from capstone_ota.monitor.service import decode_message
    raw = struct.pack('=IB3x8s', 0x200, 8, bytes(range(8)))
    anc = [(socket.SOL_SOCKET, 35, struct.pack('ll', 1730000000, 250000000))]
    frame = decode_message(raw, anc, 'can0')
    assert frame == trace.CanFrame(1730000000.25, 'can0', 0x200, bytes(range(8)))
    assert decode_message(raw[:8], anc, 'can0') is None                       # short read
    assert decode_message(raw, [], 'can0') is None                            # no timestamp: not trusted
    ext = struct.pack('=IB3x8s', 0x80000200, 8, bytes(8))
    assert decode_message(ext, anc, 'can0') is None                           # extended frames ignored


def test_verdict_shows_current_delay_and_the_declared_limit(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    healthy = drive(monitor, simulate(ROWS, mode='normal', seed=8, t0=T0), until=T0 + 60)[-1]
    assert healthy['latency_limit_ms'] == pytest.approx(100.0)
    assert healthy['recent_delay_ms'] is not None and healthy['recent_delay_ms'] < 30
    monitor = Monitor(baseline)
    monitor.reset(T0 - 0.5)
    verdicts = drive(monitor, simulate(ROWS, mode='delay', seed=8, start=40.0, t0=T0), until=T0 + 70)
    assert verdicts[-1]['recent_delay_ms'] > verdicts[-1]['latency_limit_ms']
    quiet = Monitor(baseline)
    quiet.reset(T0)
    assert quiet.tick(T0 + 1)['recent_delay_ms'] is None             # no data yet: unknown, never zero


def test_reset_nonce_is_echoed_so_a_requester_can_prove_its_reset_was_applied(baseline):
    monitor = Monitor(baseline)
    monitor.reset(T0, nonce='abc123')
    assert monitor.tick(T0 + 1)['reset_nonce'] == 'abc123'
    monitor.reset(T0 + 5)
    assert monitor.tick(T0 + 6)['reset_nonce'] is None
