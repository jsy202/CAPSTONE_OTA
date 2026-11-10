"""Windowed timing metrics derived from paired source/echo observations."""
from __future__ import annotations

import math
from typing import NamedTuple, Optional


def quantile(values, q: float) -> float:
    if not values:
        raise ValueError('quantile of empty data')
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def mean_std(values) -> tuple[float, float]:
    mean = sum(values) / len(values)
    return mean, math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


class Baseline(NamedTuple):
    lat_mean: float
    lat_std: float
    lat_p99: float
    max_gap: float
    skip_ratio: float
    iat_std: float


class WindowMetrics(NamedTuple):
    t0: float
    t1: float
    n_sources: int
    n_pairs: int
    lat_p50: Optional[float]
    lat_p99: Optional[float]
    max_gap: Optional[float]
    skip_ratio: float
    iat_std: Optional[float]
    slope: Optional[float]       # seconds of latency per second of time
    slope_t: Optional[float]     # t-statistic of the slope
    latencies: tuple


def _source_times(result) -> list[float]:
    return sorted([p.t_source for p in result.pairs] + [s.t for s in result.unmatched_sources])


def _gaps(echo_times) -> Optional[float]:
    times = sorted(echo_times)
    return max((b - a for a, b in zip(times, times[1:])), default=None)


def _iat_std(times) -> Optional[float]:
    if len(times) < 3:
        return None
    return mean_std([b - a for a, b in zip(times, times[1:])])[1]


def regression(points) -> tuple[Optional[float], Optional[float]]:
    """Least-squares slope and its t-statistic for (t, latency) points."""
    n = len(points)
    if n < 5:
        return None, None
    mt = sum(t for t, _ in points) / n
    mv = sum(v for _, v in points) / n
    sxx = sum((t - mt) ** 2 for t, _ in points)
    if sxx == 0:
        return None, None
    slope = sum((t - mt) * (v - mv) for t, v in points) / sxx
    sse = sum((v - mv - slope * (t - mt)) ** 2 for t, v in points)
    if sse <= 1e-18:
        return slope, 0.0 if slope == 0 else math.inf
    return slope, slope / math.sqrt(sse / (n - 2) / sxx)


def _within(times, limit: float):
    """Consecutive differences of a sorted series, dropping holes longer than `limit` seconds."""
    times = sorted(times)
    return [b - a for a, b in zip(times, times[1:]) if b - a <= limit]


def fit_baseline(*results, hole: float = 2.0) -> Baseline:
    """Reference behaviour of a healthy build from one or more continuous healthy traces.

    Gaps longer than `hole` seconds (between separate recordings) are not treated as freezes.
    """
    pairs = [p for r in results for p in r.pairs]
    if len(pairs) < 20:
        raise ValueError('baseline needs at least 20 paired frames')
    latencies = [p.latency for p in pairs]
    mean, std = mean_std(latencies)
    echo_gaps, iats, sources, unmatched = [], [], 0, 0
    for r in results:
        echo_gaps += _within((p.t_echo for p in r.pairs), hole)
        times = _source_times(r)
        iats += _within(times, hole)
        sources += len(times)
        unmatched += len(r.unmatched_sources)
    return Baseline(mean, std, quantile(latencies, 0.99), max(echo_gaps, default=0.0),
                    unmatched / max(1, sources), mean_std(iats)[1] if len(iats) > 2 else 0.0)


def window_metrics(result, t0: float, t1: float) -> WindowMetrics:
    pairs = [p for p in result.pairs if t0 <= p.t_source < t1]
    sources = [t for t in _source_times(result) if t0 <= t < t1]
    unmatched = [s for s in result.unmatched_sources if t0 <= s.t < t1]
    latencies = [p.latency for p in pairs]
    slope, slope_t = regression([(p.t_source, p.latency) for p in pairs])
    return WindowMetrics(
        t0, t1, len(sources), len(pairs),
        quantile(latencies, 0.5) if latencies else None,
        quantile(latencies, 0.99) if latencies else None,
        _gaps(p.t_echo for p in pairs),
        len(unmatched) / len(sources) if sources else 0.0,
        _iat_std(sources), slope, slope_t, tuple(latencies))
