"""Explainable windowed detectors, fault-signature classification and confirmation.

Rules count as separate votes, but latency p99 / CUSUM / drift are statistics of the same latency
series: agreement improves robustness against one threshold choice, it is not statistical
independence. Thresholds below are development defaults; real values come from calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional

from .metrics import Baseline, WindowMetrics, window_metrics

ACTIONABLE = ('delay', 'freeze')


@dataclass(frozen=True)
class Thresholds:
    # Declared requirement: the cluster must process vehicle frames within one CAN cycle (100 ms).
    # Above this the next frame arrives before the previous one is done, so frames start to queue/skip.
    latency_limit: float = 0.100
    p99_factor: float = 3.0
    p99_margin: float = 0.05
    gap_factor: float = 3.0
    gap_floor: float = 0.35
    skip_margin: float = 0.05
    cusum_shift: float = 0.04       # smallest latency increase worth detecting, seconds (slack = half)
    cusum_h: float = 0.2            # alarm level for the accumulated excess, seconds
    slope_min: float = 0.001        # seconds of latency growth per second
    slope_t: float = 5.0
    iat_std_factor: float = 3.0
    iat_std_margin: float = 0.005
    window: float = 10.0
    step: float = 5.0
    min_sources: int = 20


class Assessment(NamedTuple):
    t0: float
    t1: float
    metrics: WindowMetrics
    fired: tuple
    votes: int
    cls: str


class Confirmation(NamedTuple):
    cls: str
    t: float            # end of the window that completed the streak
    windows: int
    fired: tuple


def cusum_peak(values, mu0: float, shift: float) -> float:
    """One-sided CUSUM peak (seconds) for a latency increase of about `shift` over the baseline mean.

    Increases much smaller than `shift` never accumulate, so run-to-run wobble (CPU frequency etc.)
    is ignored; sustained increases of the configured size alarm within a handful of samples.
    """
    level = peak = 0.0
    for x in values:
        level = max(0.0, level + (x - mu0) - shift / 2)
        peak = max(peak, level)
    return peak


def fired_rules(m: WindowMetrics, base: Baseline, th: Thresholds) -> tuple:
    fired = []
    if m.lat_p99 is not None and m.lat_p99 > th.latency_limit:
        fired.append('latency_limit')
    if m.lat_p99 is not None and m.lat_p99 > max(th.p99_factor * base.lat_p99, base.lat_p99 + th.p99_margin):
        fired.append('latency_p99')
    if m.latencies and cusum_peak(m.latencies, base.lat_mean, th.cusum_shift) > th.cusum_h:
        fired.append('cusum')
    if m.slope is not None and m.slope > th.slope_min and m.slope_t is not None and m.slope_t > th.slope_t:
        fired.append('drift')
    if m.max_gap is not None and m.max_gap > max(th.gap_factor * base.max_gap, th.gap_floor):
        fired.append('freeze_gap')
    if m.skip_ratio > base.skip_ratio + th.skip_margin:
        fired.append('skip_ratio')
    if m.iat_std is not None and m.iat_std > th.iat_std_factor * base.iat_std + th.iat_std_margin:
        fired.append('input_jitter')
    return tuple(fired)


def classify(fired) -> str:
    """Fault signature: freeze (stalls/skips), delay (late echo), input anomaly (source jitter only)."""
    names = set(fired)
    if names & {'freeze_gap', 'skip_ratio'}:
        return 'freeze'
    if names & {'latency_limit', 'latency_p99', 'cusum', 'drift'}:
        return 'delay'
    if 'input_jitter' in names:
        return 'input_anomaly'
    return 'normal'


def assess_window(result, base: Baseline, th: Thresholds, t0: float) -> Assessment:
    m = window_metrics(result, t0, t0 + th.window)
    if m.n_sources < th.min_sources:
        return Assessment(t0, t0 + th.window, m, (), 0, 'insufficient')
    fired = fired_rules(m, base, th)
    return Assessment(t0, t0 + th.window, m, fired, len(fired), classify(fired))


def assess_trace(result, base: Baseline, th: Thresholds = Thresholds()) -> list[Assessment]:
    times = [p.t_source for p in result.pairs] + [s.t for s in result.unmatched_sources]
    if not times:
        return []
    start, finish = min(times), max(times)
    out, begin = [], start
    while begin + th.window <= finish + 1e-9:
        out.append(assess_window(result, base, th, begin))
        begin += th.step
    return out


def first_suspect(assessments) -> Optional[Assessment]:
    return next((a for a in assessments if a.votes >= 1 and a.cls != 'insufficient'), None)


def confirm(assessments, persist: int = 3, min_votes: int = 2) -> Optional[Confirmation]:
    """Confirm after `persist` consecutive windows of the same non-normal class with enough votes."""
    streak, cls = 0, None
    for item in assessments:
        # A violated declared requirement is itself the verdict (after persistence); heuristic statistics
        # need agreement from `min_votes` rules; input anomalies are not actionable anyway.
        needed = 1 if item.cls == 'input_anomaly' or 'latency_limit' in item.fired else min_votes
        if item.cls not in ('normal', 'insufficient') and item.votes >= needed:
            streak = streak + 1 if item.cls == cls else 1
            cls = item.cls
            if streak >= persist:
                return Confirmation(cls, item.t1, streak, item.fired)
        else:
            streak, cls = 0, None
    return None
