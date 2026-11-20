"""Does a faster cockpit capture disturb the Pi3 measurement of the cluster?

Runs the real chain (cluster on core 0, drive profile, Pi3 monitor) on normal driving with no update and no
attack, and samples Pi3's own verdict while the cockpit capture runs at different intervals. Order is
none, 0.5 s, 0.1 s, none again, so a slow drift shows up as the two "none" runs differing.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from bench import incident_field as field


def summarize(rows):
    """Spread of what the Pi3 monitor measured. `None` rows are reads that failed (a slow ssh), not zeros."""
    rows = [r for r in rows]
    seen = [r for r in rows if r]
    delays = [r["recent_delay_ms"] for r in seen if r.get("recent_delay_ms") is not None]
    p99s = [r["last_window"]["lat_p99_ms"] for r in seen if r.get("last_window")]
    return {"n": len(seen), "missing": len(rows) - len(seen),
            "delay_median_ms": statistics.median(delays) if delays else None,
            "delay_max_ms": max(delays) if delays else None,
            "p99_max_ms": max(p99s) if p99s else None,
            "not_observing": sum(1 for r in seen if r.get("state") != "observing")}


def sample(pi3, seconds, step=2.0):
    end, rows = time.time() + seconds, []
    while time.time() < end:
        rows.append(field.pi3_verdict(pi3))
        time.sleep(step)
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args(argv)
    config, boards = field.load(args.config)
    baseline = field.pi2_status(boards["pi2"])
    field.require_baseline(baseline, config)
    field.check_committed_slot(boards["pi2"])
    results = []
    field.start_real_services(boards, print)
    try:
        pi3_ip = field.pi3_address(boards["pi3"])
        if not field.wait_for(lambda: field.healthy(field.pi3_verdict(boards["pi3"])), 90, 3.0):
            raise SystemExit("the Pi3 monitor does not see both the vehicle input and the cluster answer")
        for name, interval in (("none", None), ("0.5s", 0.5), ("0.1s", 0.1), ("none-again", None)):
            field.stop_screen(boards)
            if interval is not None:
                field.start_screen(boards, pi3_ip, print, interval)
            time.sleep(15)  # settle: the monitor's windows must be measured under this setting only
            rows = sample(boards["pi3"], args.seconds)
            results.append({"setting": name, "interval": interval, **summarize(rows)})
            print(json.dumps(results[-1]), flush=True)
    finally:
        field.stop_screen(boards)
        field.stop_real_services(boards)
        args.output.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
