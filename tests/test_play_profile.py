"""Profile playback keeps an absolute schedule (no cumulative drift) and a continuous counter."""
import pytest

from bench import drive_cycle as dc
from bench import play_profile as pp


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def play(rows, **kw):
    fake, sent = FakeClock(), []
    count = pp.play(rows, lambda m, at: sent.append((at, m)), clock=fake.clock, sleep=fake.sleep, **kw)
    return fake, sent, count


def test_sends_every_row_on_an_absolute_schedule():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE)[:20]
    fake, sent, count = play(rows)
    assert count == 20 and len(sent) == 20
    assert [round(at - 100.0, 6) for at, _ in sent] == pytest.approx([i * 0.1 for i in range(20)])


def test_slow_sender_does_not_accumulate_drift():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE)[:20]
    fake = FakeClock()
    sent = []

    def slow(message, at):
        fake.now += 0.03                      # each send itself takes 30 ms
        sent.append(at)

    pp.play(rows, slow, clock=fake.clock, sleep=fake.sleep)
    assert sent[-1] - 100.0 == pytest.approx(19 * 0.1, abs=0.04)   # still on the absolute grid


def test_counter_is_continuous_across_loops_and_wraps():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE)[:200]
    _, sent, count = play(rows, loops=2)
    counters = [bytes.fromhex(m['data'])[6] for _, m in sent]
    assert count == 400
    assert counters[:3] == [0, 1, 2] and counters[255:258] == [255, 0, 1]
    assert counters[200] == 200                 # second loop continues, it does not restart at 0


def test_stop_flag_ends_playback_early():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE)[:50]
    fake, sent = FakeClock(), []
    flag = {'stop': False}

    def send(message, at):
        sent.append(at)
        flag['stop'] = len(sent) >= 5

    count = pp.play(rows, send, clock=fake.clock, sleep=fake.sleep, should_stop=lambda: flag['stop'])
    assert count == 5


def _speeds(sent):
    return [int.from_bytes(bytes.fromhex(m['data'])[0:2], 'big') for _, m in sent]


def test_while_held_the_vehicle_stands_still_and_the_profile_resumes_where_it_stopped():
    """During a rollback the car stays stopped (the cluster is off for a minute); when it comes back the profile goes on
    from the standstill instead of showing the speed it would have reached meanwhile."""
    rows = [dc.Row(i * 0.1, speed, 2000, 3) for i, speed in enumerate([0, 0, 300, 600, 900, 1200])]
    state = {'ticks': 0}

    def hold():
        state['ticks'] += 1
        return 2 <= state['ticks'] <= 5            # four ticks held, starting at the third tick
    fake, sent, count = play(rows, hold=hold)
    assert _speeds(sent)[:1] == [0]                       # row 0
    assert _speeds(sent)[1:5] == [0, 0, 0, 0]             # four held frames: the profile did not move
    assert _speeds(sent)[5:] == [0, 300, 600, 900, 1200]  # resumed exactly where it stopped (row 1 next, then the climb)
    counters = [bytes.fromhex(m['data'])[6] for _, m in sent]
    assert counters == list(range(len(sent)))             # one continuous counter: the bus never looks silent
    held_gears = [bytes.fromhex(m['data'])[4] for _, m in sent[1:5]]
    assert held_gears == [0, 0, 0, 0]                     # neutral while held
    assert bytes.fromhex(sent[5][1]['data'])[4] == 3      # and the profile's own gear again afterwards


def test_without_a_hold_nothing_changes():
    rows = dc.build_profile(dc.SYNTHETIC_CYCLE)[:30]
    _, plain, _ = play(rows)
    _, held, _ = play(rows, hold=lambda: False)
    assert [m for _, m in plain] == [m for _, m in held]


def test_a_hold_file_is_a_hold_while_it_exists(tmp_path):
    marker = tmp_path / 'profile.hold'
    hold = pp.file_hold(marker)
    assert hold() is False
    marker.write_text('')
    assert hold() is True
    marker.unlink()
    assert hold() is False
