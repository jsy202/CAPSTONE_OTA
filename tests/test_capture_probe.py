"""bench.capture_probe: does a faster cockpit capture disturb the Pi3 measurement? (summary logic, no board)"""
from bench.capture_probe import summarize


def _sample(delay, p99, state="observing", skip=0.0):
    return {"state": state, "recent_delay_ms": delay, "last_window": {"lat_p99_ms": p99, "skip_ratio": skip}}


def test_summary_reports_spread_not_just_an_average():
    rows = [_sample(d, p) for d, p in [(3.0, 3.5), (4.0, 4.2), (5.0, 4.4), (40.0, 90.0)]]
    s = summarize(rows)
    assert s["n"] == 4 and s["delay_max_ms"] == 40.0 and s["p99_max_ms"] == 90.0
    assert s["delay_median_ms"] == 4.5 and s["not_observing"] == 0


def test_summary_counts_samples_where_the_monitor_left_observing_and_ignores_missing_reads():
    rows = [_sample(3.0, 3.0), _sample(3.0, 3.0, state="suspect"), None, {"state": "observing", "recent_delay_ms": None,
                                                                          "last_window": None}]
    s = summarize(rows)
    assert s["n"] == 3 and s["not_observing"] == 1 and s["missing"] == 1
    assert summarize([None])["n"] == 0
