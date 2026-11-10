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
