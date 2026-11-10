"""Probation supervisor: pass only after enough monitored time; roll back only inside a safe window."""
import pytest

from capstone_ota.probation import Probation


class World:
    """Fake clock plus scripted Pi3 verdicts and Pi2 cluster speed."""
    def __init__(self):
        self.now = 0.0
        self.seq = 0
        self.reset_calls = 0
        self.reset_id = 'old'
        self.reset_nonce = 'stale'
        self.verdict_state = 'observing'
        self.monitored_per_s = 1.0
        self.monitored = 40.0                  # leftover from a previous probation until reset is acknowledged
        self.bus_stopped = 0.0
        self.cluster = {'model_speed': 50.0, 'valid': True}
        self.fetch_error = False
        self.frozen_seq = False
        self.on_tick = lambda w: None

    def clock(self): return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.monitored += self.monitored_per_s * seconds if self.reset_calls else 0
        if not self.frozen_seq:
            self.seq += 1
        self.on_tick(self)

    def reset(self, nonce):
        self.reset_calls += 1
        self.reset_id, self.reset_nonce, self.monitored = 'new', nonce, 0.0

    def fetch(self):
        if self.fetch_error:
            raise OSError('ssh failed')
        return {'seq': self.seq, 'state': self.verdict_state, 'reset_id': self.reset_id, 'reset_nonce': self.reset_nonce, 'cls': 'delay',
                'fired': ['latency_p99', 'cusum'], 'monitored_s': self.monitored, 'bus_stopped_s': self.bus_stopped,
                'bus_speed': 500, 'confirm_t': 123.0}

    def speed(self):
        return None if self.cluster is None else self.cluster


def make(world, **kw):
    args = dict(probation_s=20, safe_s=3, max_wait_s=120, max_total_s=100, poll_s=1, stale_s=4)
    args.update(kw)
    return Probation(world.fetch, world.reset, world.speed, clock=world.clock, sleep=world.sleep, **args)


def test_healthy_probation_passes_only_after_enough_monitored_time_since_reset():
    world = World()
    result = make(world).run()
    assert result['passed'] is True and world.reset_calls == 1
    assert 19 <= world.now <= 30                      # the stale 40 s from the old reset id did not count
    assert result['observed'][0]['passed'] is True


def test_confirmed_anomaly_waits_for_both_observers_to_report_standstill():
    world = World()
    stop = {'at': 30.0}

    def script(w):
        if w.now >= 8:
            w.verdict_state = 'confirmed'
        if w.now >= stop['at']:
            w.cluster = {'model_speed': 0.0, 'valid': True}
            w.bus_stopped += 1.0

    world.on_tick = script
    result = make(world).run()
    assert result['passed'] is False and 'confirmed delay' in result['error']
    assert world.now >= stop['at'] + 3                # returned only after safe_s of standstill
    assert result['observed'][0]['passed'] is False and result['observed'][0]['cls'] == 'delay'


def test_one_observer_alone_does_not_open_the_safe_window():
    world = World()
    world.verdict_state = 'confirmed'
    world.cluster = {'model_speed': 0.0, 'valid': True}      # Pi2 says stopped, Pi3 bus says moving
    result = make(world, max_wait_s=15).run()
    assert result['passed'] is False and 'safe_window_timeout' in result['error']
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0                                  # Pi3 says stopped, Pi2 cluster says moving
    assert 'safe_window_timeout' in make(world, max_wait_s=15).run()['error']


def test_unknown_cluster_state_never_counts_as_stopped():
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0
    world.cluster = None
    assert 'safe_window_timeout' in make(world, max_wait_s=10).run()['error']


def test_stale_or_failing_monitor_never_passes_and_fails_closed_at_the_cap():
    for tweak in ('frozen', 'error'):
        world = World()
        world.bus_stopped = 99.0
        world.cluster = {'model_speed': 0.0, 'valid': True}
        if tweak == 'frozen':
            world.frozen_seq = True
        else:
            world.fetch_error = True
        result = make(world, max_total_s=30).run()
        assert result['passed'] is False and 'unmonitored' in result['error']


def test_blind_time_is_not_counted_toward_probation():
    world = World()

    def script(w):
        w.verdict_state = 'blind' if 5 <= w.now < 25 else 'observing'
        w.monitored_per_s = 0.0 if w.verdict_state == 'blind' else 1.0

    world.on_tick = script
    result = make(world).run()
    assert result['passed'] is True and world.now >= 40       # 20 s monitored + 20 s blind


def test_reset_must_be_acknowledged_before_counting():
    world = World()
    world.reset = lambda nonce: setattr(world, 'reset_calls', world.reset_calls + 1)   # Pi3 never echoes the nonce
    result = make(world, max_total_s=25).run()
    assert result['passed'] is False and 'unmonitored' in result['error']


# ---- observation starvation: a starved cluster answers only some requests ----

def test_intermittent_cluster_failures_do_not_reset_the_safe_window():
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0
    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        return None if calls['n'] % 3 else {'model_speed': 0.0, 'valid': True}      # answers 1 request in 3

    world.speed = flaky
    result = make(world, max_wait_s=40, obs_grace=3.0).run()
    assert 'rolled back in safe window' in result['error'] and 'safe_window_timeout' not in result['error']


def test_a_known_moving_observation_resets_the_safe_window_even_inside_the_grace():
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0
    seq = iter([{'model_speed': 0.0, 'valid': True}, {'model_speed': 0.0, 'valid': True}, {'model_speed': 30.0, 'valid': True}])
    world.speed = lambda: next(seq, {'model_speed': 30.0, 'valid': True})
    assert 'safe_window_timeout' in make(world, max_wait_s=15, obs_grace=3.0).run()['error']


def test_stale_stopped_observation_expires():
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0
    calls = {'n': 0}

    def once_then_silence():
        calls['n'] += 1
        return {'model_speed': 0.0, 'valid': True} if calls['n'] == 1 else None

    world.speed = once_then_silence
    assert 'safe_window_timeout' in make(world, max_wait_s=20, obs_grace=2.0).run()['error']


def test_every_safe_window_decision_is_traced_for_evidence():
    world = World()
    world.verdict_state = 'confirmed'
    world.bus_stopped = 99.0
    world.cluster = {'model_speed': 0.0, 'valid': True}
    rows = []
    result = Probation(world.fetch, world.reset, world.speed, clock=world.clock, sleep=world.sleep, probation_s=20, safe_s=3,
                       max_wait_s=60, max_total_s=100, poll_s=1, stale_s=4, trace=rows.append).run()
    assert 'rolled back in safe window' in result['error']
    assert rows and {'t', 'own_stopped', 'bus_stopped', 'watching', 'safe_since'} <= set(rows[0])
    assert rows[-1]['own_stopped'] is True and rows[-1]['bus_stopped'] is True


def test_decision_is_fast_when_the_wait_loop_polls_quickly():
    world = World()
    world.verdict_state = 'confirmed'
    stop_at = 30.0

    def script(w):
        if w.now >= stop_at:
            w.cluster = {'model_speed': 0.0, 'valid': True}
            w.bus_stopped = w.now - stop_at + 6.0            # Pi3's stop timer already ahead of the local one

    world.cluster = {'model_speed': 50.0, 'valid': True}
    world.on_tick = script
    result = Probation(world.fetch, world.reset, world.speed, clock=world.clock, sleep=world.sleep, probation_s=20, safe_s=3,
                       max_wait_s=120, max_total_s=100, poll_s=1, stale_s=4, wait_poll_s=0.25).run()
    assert 'rolled back in safe window' in result['error']
    assert world.now <= stop_at + 3 + 1.0                    # decided within one second of safe_s


def test_a_stale_confirmed_verdict_from_before_the_reset_is_never_mistaken_for_the_new_probation():
    """Regression: the Pi3 monitor had not processed the reset yet and still reported the previous (confirmed) state."""
    world = World()
    world.verdict_state = 'confirmed'
    world.reset_id = 'previous-probation'
    world.reset_nonce = 'nonce-of-the-previous-probation'
    world.reset = lambda nonce: setattr(world, 'reset_calls', world.reset_calls + 1)      # Pi3 is slow: nothing changes yet
    result = make(world, max_total_s=12).run()
    assert 'unmonitored' in result['error'] and 'confirmed freeze' not in result['error']
    # ...and once Pi3 does echo our nonce, its (new) verdict is trusted
    fresh = World()
    assert make(fresh).run()['passed'] is True
