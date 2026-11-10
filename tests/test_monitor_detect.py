"""Offline detectors against synthetic traces: seeded faults must be found, healthy traces must not."""
import math

import pytest

from bench import drive_cycle as dc
from bench.trace_sim import simulate
from capstone_ota.monitor import detect, metrics, trace

START = 40.0
T0 = 1000.0
CYCLE_A = dc.SYNTHETIC_CYCLE
CYCLE_B = [(t * 1.3, v * 1.15) for t, v in dc.SYNTHETIC_CYCLE]   # different shape for evaluation


def paired(cycle, mode, seed=0):
    return trace.pair_frames(simulate(dc.build_profile(cycle), mode=mode, seed=seed, start=START, t0=T0))


@pytest.fixture(scope='module')
def baseline():
    return metrics.fit_baseline(paired(CYCLE_A, 'normal', seed=100))


def test_quantile_and_regression_basics():
    assert metrics.quantile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert metrics.quantile([5], 0.99) == 5
    with pytest.raises(ValueError):
        metrics.quantile([], 0.5)
    slope, t = metrics.regression([(i, 0.5 * i) for i in range(10)])
    assert slope == pytest.approx(0.5) and math.isinf(t)
    assert metrics.regression([(1, 1)] * 3) == (None, None)


def test_baseline_needs_enough_data():
    with pytest.raises(ValueError):
        metrics.fit_baseline(trace.PairResult([], [], [], 0))


def test_baseline_describes_healthy_build(baseline):
    assert baseline.lat_mean == pytest.approx(0.012, abs=0.002)
    assert baseline.lat_p99 < 0.03 and baseline.skip_ratio == 0.0 and baseline.max_gap < 0.2


@pytest.mark.parametrize('cycle', [CYCLE_A, CYCLE_B])
def test_healthy_traces_never_confirm_and_rarely_raise_suspicion(baseline, cycle):
    """A lone CUSUM blip may raise suspicion (measured 4/60 traces); it must never be confirmed."""
    suspected = 0
    for seed in range(60):
        assessments = detect.assess_trace(paired(cycle, 'normal', seed), baseline)
        assert detect.confirm(assessments) is None, seed
        assert all(a.votes <= 1 for a in assessments), seed       # healthy data never gets two agreeing rules
        suspected += detect.first_suspect(assessments) is not None
    assert suspected <= 9                                          # <= 15% of healthy traces


@pytest.mark.parametrize('cycle', [CYCLE_A, CYCLE_B])
@pytest.mark.parametrize('mode,cls', [('delay', 'delay'), ('freeze', 'freeze'), ('drift', 'delay')])
def test_seeded_faults_are_confirmed_with_correct_class(baseline, cycle, mode, cls):
    for seed in range(5):
        assessments = detect.assess_trace(paired(cycle, mode, seed), baseline)
        suspect = detect.first_suspect(assessments)
        confirmation = detect.confirm(assessments)
        assert suspect is not None and confirmation is not None, (mode, seed)
        assert confirmation.cls == cls and confirmation.cls in detect.ACTIONABLE
        th = detect.Thresholds()
        # Window grid is anchored at the first source frame (ms jitter), so allow a small tolerance.
        assert suspect.t1 - (T0 + START) <= th.window + 0.05
        # A 2 ms/s ramp needs ~20 s to reach the 40 ms effect size, so drift confirms later (measured 25 s).
        limit = 30.0 if mode == 'drift' else th.window + 2 * th.step + 0.05           # 20 s with defaults
        assert confirmation.t - (T0 + START) <= limit


def test_no_alarm_before_fault_onset(baseline):
    assessments = detect.assess_trace(paired(CYCLE_A, 'delay', 1), baseline)
    assert all(a.cls == 'normal' for a in assessments if a.t1 <= T0 + START)


def test_drift_is_caught_by_the_trend_rule_before_the_tail_rule(baseline):
    assessments = detect.assess_trace(paired(CYCLE_A, 'drift', 2), baseline)
    first = next(a for a in assessments if a.votes)
    assert 'drift' in first.fired and 'latency_p99' not in first.fired    # trend rule is the early warning
    assert 'cusum' in detect.confirm(assessments).fired                    # cumulative rule joins by confirmation


def test_input_jitter_is_not_actionable(baseline):
    assessments = detect.assess_trace(paired(CYCLE_A, 'input', 3), baseline)
    confirmation = detect.confirm(assessments)
    assert confirmation is not None and confirmation.cls == 'input_anomaly'
    assert confirmation.cls not in detect.ACTIONABLE
    assert not any(a.cls in detect.ACTIONABLE for a in assessments)


def test_sparse_source_is_insufficient_not_normal(baseline):
    frames = simulate(dc.build_profile(CYCLE_A), mode='normal', seed=1, t0=T0)
    sparse = [f for f in frames if f.t < T0 + 3 or f.t > T0 + 90]    # Central goes silent in between
    assessments = detect.assess_trace(trace.pair_frames(sparse), baseline)
    assert any(a.cls == 'insufficient' for a in assessments)
    assert detect.confirm(assessments) is None


def test_confirm_requires_persistence_and_votes(baseline):
    assessments = detect.assess_trace(paired(CYCLE_A, 'delay', 4), baseline)
    bad = [a for a in assessments if a.cls == 'delay']
    assert len(bad) >= 3
    assert detect.confirm(bad[:2]) is None                                   # not persistent enough
    heuristic_only = [a._replace(fired=tuple(f for f in a.fired if f != 'latency_limit')) for a in bad]
    assert detect.confirm(heuristic_only, persist=3, min_votes=99) is None  # heuristics need enough agreeing rules
    heuristic = [f for f in bad[0].fired if f != 'latency_limit'][:1]
    single = [a._replace(fired=tuple(heuristic), votes=1) for a in bad]
    assert detect.confirm(single, min_votes=2) is None                       # one heuristic rule alone: no
    only_limit = [a._replace(fired=('latency_limit',), votes=1) for a in bad]
    assert detect.confirm(only_limit, min_votes=2).cls == 'delay'            # the declared requirement alone: yes


def test_text_roundtrip_gives_same_pairs(baseline):
    frames = simulate(dc.build_profile(CYCLE_A), mode='delay', seed=5, start=START, t0=T0)
    text = '\n'.join(trace.format_candump_line(f) for f in frames)
    again = trace.pair_frames(trace.parse_candump(text))
    original = trace.pair_frames(frames)
    assert len(again.pairs) == len(original.pairs)
    assert detect.confirm(detect.assess_trace(again, baseline)).cls == 'delay'


# ---- real-data lessons (Gate 0 traces): effect-size CUSUM and gap-robust baseline ----

def test_baseline_ignores_gaps_between_separate_healthy_segments():
    first = paired(CYCLE_A, 'normal', seed=200)
    shifted = trace.pair_frames([f._replace(t=f.t + 5000.0) for f in
                                 simulate(dc.build_profile(CYCLE_A), mode='normal', seed=201, start=START, t0=T0)])
    base = metrics.fit_baseline(first, shifted)
    assert base.max_gap < 0.3 and base.iat_std < 0.01       # not inflated by the 5000 s hole
    one_trace_with_hole = trace.PairResult(first.pairs + shifted.pairs, [], [], 0)
    assert metrics.fit_baseline(one_trace_with_hole).max_gap < 0.3   # long holes are ignored too


def test_cusum_ignores_small_shifts_but_catches_the_configured_effect_size():
    th = detect.Thresholds()
    mean = 0.002
    assert detect.cusum_peak([mean + 0.001] * 500, mean, th.cusum_shift) < th.cusum_h        # +1 ms forever: ignore
    assert detect.cusum_peak([mean + 0.060] * 20, mean, th.cusum_shift) > th.cusum_h         # +60 ms: alarm
    assert detect.cusum_peak([mean - 0.001] * 50, mean, th.cusum_shift) == 0.0               # faster is never bad


# ---- declared requirement: cluster processing delay (p99 over a window) must stay within one CAN cycle ----

def test_declared_delay_limit_is_one_can_cycle_and_is_an_error_above_it(baseline):
    th = detect.Thresholds()
    assert th.latency_limit == pytest.approx(0.100)                 # the 100 ms CAN period of the vehicle frame
    fired_when_delayed = set()
    for a in detect.assess_trace(paired(CYCLE_A, 'delay', 3), baseline):
        fired_when_delayed |= set(a.fired)
    assert 'latency_limit' in fired_when_delayed
    for seed in range(30):                                          # healthy traces never violate the requirement
        for a in detect.assess_trace(paired(CYCLE_A, 'normal', seed), baseline):
            assert 'latency_limit' not in a.fired


def test_limit_is_configurable_and_a_violation_counts_as_delay():
    result = paired(CYCLE_A, 'normal', 5)
    base = metrics.fit_baseline(paired(CYCLE_A, 'normal', 101))
    strict = detect.Thresholds(latency_limit=0.0005)               # below the healthy 12 ms latency
    assert all('latency_limit' in a.fired for a in detect.assess_trace(result, base, strict) if a.cls != 'insufficient')
    assert detect.classify(('latency_limit',)) == 'delay'
    assert detect.confirm(detect.assess_trace(result, base, strict)).cls == 'delay'
    lax = detect.Thresholds(latency_limit=10.0)
    assert all('latency_limit' not in a.fired for a in detect.assess_trace(paired(CYCLE_A, 'delay', 5), base, lax))
