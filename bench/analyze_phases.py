"""Per-phase latency report from a candump trace and a phase schedule (Gate 0 load experiment)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from capstone_ota.monitor import metrics, trace


def phase_report(result, phases, margin: float = 2.0) -> list[dict]:
    """Metrics per phase; `margin` trims the phase edges because board clocks/starts are not exact."""
    rows = []
    for phase in phases:
        start, end = phase['t0'] + margin, phase['t1'] - margin
        if end <= start:
            raise ValueError('phase shorter than twice the margin')
        m = metrics.window_metrics(result, start, end)
        ms = lambda v: None if v is None else round(v * 1000, 3)
        rows.append({'name': phase['name'], 'load': phase['load'], 'n_sources': m.n_sources, 'n_pairs': m.n_pairs,
                     'lat_p50_ms': ms(m.lat_p50), 'lat_p99_ms': ms(m.lat_p99),
                     'lat_max_ms': ms(max(m.latencies)) if m.latencies else None,
                     'max_gap_ms': ms(m.max_gap), 'skip_ratio': round(m.skip_ratio, 4)})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('phases', type=Path, help='JSON list of {name, load, t0, t1} in epoch seconds')
    parser.add_argument('--margin', type=float, default=2.0)
    args = parser.parse_args(argv)
    result = trace.pair_frames(trace.parse_candump(args.trace))
    rows = phase_report(result, json.loads(args.phases.read_text()), args.margin)
    print(json.dumps({'invalid_frames': result.invalid, 'phases': rows}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
