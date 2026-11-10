"""Fast on-board delay readout for the display (not an independent verdict)."""
import pytest

from bench import drive_cycle as dc
from bench.trace_sim import simulate
from capstone_ota.monitor.tap import DelayTap

T0 = 1000.0
ROWS = dc.build_profile(dc.SYNTHETIC_CYCLE)


def run(mode, until, **kw):
    tap = DelayTap()
    frames = sorted(simulate(ROWS, mode=mode, seed=3, t0=T0, **kw), key=lambda f: f.t)
    i, snap = 0, None
    now = T0 + 1
    while now <= until:
        while i < len(frames) and frames[i].t <= now:
            tap.feed(frames[i]); i += 1
        snap = tap.snapshot(now)
        now += 0.2
    return snap


def test_healthy_traffic_reads_a_small_delay():
    snap = run('normal', T0 + 30)
    assert snap['delay_ms'] < 30 and snap['n'] >= 10


def test_a_delay_fault_shows_up_within_about_a_second():
    assert run('delay', T0 + 41.5, start=40.0)['delay_ms'] > 100          # fault began at +40 s
    assert run('delay', T0 + 39, start=40.0)['delay_ms'] < 30             # and was invisible before


def test_no_data_is_unknown_not_zero():
    assert DelayTap().snapshot(T0) == {'delay_ms': None, 'max_ms': None, 'n': 0, 'can': None, 'cluster': None}


def test_old_frames_age_out_of_the_window():
    tap = DelayTap(window=2.0)
    for f in simulate(ROWS, mode='delay', seed=3, t0=T0, start=0.0):
        if f.t < T0 + 10:
            tap.feed(f)
    assert tap.snapshot(T0 + 9.9)['delay_ms'] > 100
    assert tap.snapshot(T0 + 30)['delay_ms'] is None                       # nothing recent


# ---- supervisor's own standstill observation: straight from the bus, not from the (starved) cluster ----

def test_speed_tracker_reports_the_latest_speed_and_how_stale_it_is():
    from capstone_ota.monitor.tap import SpeedTracker
    from capstone_ota.monitor import trace
    from capstone_ota.qt_source import frame
    tracker = SpeedTracker()
    assert tracker.snapshot(T0) == {'model_speed': None, 'valid': False}                 # nothing seen: unknown
    def f(t, kmh10, ident=0x200):
        return trace.CanFrame(t, 'can0', ident, bytes.fromhex(frame(kmh10, 1)['data']))
    tracker.feed(f(T0, 0)); tracker.feed(f(T0 + 0.1, 0))
    assert tracker.snapshot(T0 + 0.5) == {'model_speed': 0.0, 'valid': True}
    tracker.feed(f(T0 + 0.2, 305))
    assert tracker.snapshot(T0 + 0.6) == {'model_speed': 30.5, 'valid': True}
    assert tracker.snapshot(T0 + 5.0)['valid'] is False                                   # silent bus is not "stopped"
    tracker.feed(trace.CanFrame(T0 + 6, 'can0', 0x200, b'\x00' * 8))                      # bad CRC frame is ignored
    assert tracker.snapshot(T0 + 6.1)['valid'] is False
    tracker.feed(f(T0 + 6.2, 100, ident=0x300))                                           # other ids are ignored
    assert tracker.snapshot(T0 + 6.3)['valid'] is False


def test_snapshot_carries_the_live_can_values_and_what_the_cluster_shows():
    tap = DelayTap()
    frames = sorted(simulate(ROWS, mode='normal', seed=4, t0=T0), key=lambda f: f.t)
    now = T0 + 20.0
    for f in frames:
        if f.t <= now:
            tap.feed(f)
    snap = tap.snapshot(now)
    assert snap['can']['speed'] is not None and snap['can']['rpm'] > 0 and 0 <= snap['can']['gear'] <= 6
    assert abs(snap['can']['speed'] - snap['cluster']['speed']) < 3.0 and snap['cluster']['behind_frames'] <= 1
    assert DelayTap().snapshot(T0)['can'] is None and DelayTap().snapshot(T0)['cluster'] is None


def test_a_lagging_cluster_shows_an_old_speed_and_falls_behind_in_frames():
    tap = DelayTap()
    frames = sorted(simulate(ROWS, mode='normal', seed=4, t0=T0, base_delay=1.0), key=lambda f: f.t)   # every echo 1 s late
    now = T0 + 12.0                                                     # accelerating: 6 s standstill, then 0 -> 50 km/h
    for f in frames:
        if f.t <= now:
            tap.feed(f)
    snap = tap.snapshot(now)
    assert 8 <= snap['cluster']['behind_frames'] <= 12                  # 1 s at 10 frames/s
    assert snap['can']['speed'] - snap['cluster']['speed'] >= 3.0       # the cluster still shows the speed from 1 s ago


def test_frames_still_waiting_for_an_echo_raise_the_delay_even_without_enough_pairs():
    """The worst moment must not show '—': an unanswered frame is at least as delayed as it has been waiting."""
    tap = DelayTap()
    frames = sorted(simulate(ROWS, mode='normal', seed=6, t0=T0), key=lambda f: f.t)
    for f in frames:
        if f.t <= T0 + 22.0 and not (f.id == 0x300 and f.t > T0 + 20.0):               # echoes stop at +20 s
            tap.feed(f)
    snap = tap.snapshot(T0 + 22.0)
    assert snap['delay_ms'] is not None and snap['delay_ms'] >= 1500


def test_healthy_traffic_has_no_waiting_frames_inflating_the_delay():
    tap = DelayTap()
    for f in sorted(simulate(ROWS, mode='normal', seed=6, t0=T0), key=lambda f: f.t):
        if f.t <= T0 + 30.0:
            tap.feed(f)
    values = []
    for k in range(60):                                       # sample at many phases of the 100 ms frame cycle
        values.append(tap.snapshot(T0 + 29.0 + k * 0.0167)['delay_ms'])
    assert max(v for v in values if v is not None) < 40
