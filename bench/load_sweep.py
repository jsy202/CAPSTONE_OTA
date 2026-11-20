"""Find the CPU load on Pi2 that makes the cluster late without silencing it.

    python -m bench.load_sweep CONFIG --seconds 40 --output .demo/load-sweep.json

The map in V3 starts one busy worker per core at realtime priority (SCHED_RR 10): the cluster is starved, its echo
arrives ~3 s late and even the cockpit's own Pi3 poll dies ("no signal"). This tool runs the same kind of busy workers
as a transient unit with a chosen worker count, priority and duty cycle, while Pi3 records the real vehicle/echo CAN
frames and the cockpit's data feed is polled, and prints one line per load level.

Test aid only. Every load unit ends by itself (the script and RuntimeMaxSec), so a crash cannot leave Pi2 loaded.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
import threading
import time
from pathlib import Path

from bench import analyze_phases
from bench import incident_field as field
from capstone_ota.monitor import trace

LATENCY_LIMIT_MS = 100.0      # the monitor's limit (what the panel shows)
SEVERE_MS = 1000.0
COCKPIT_FEED = "/run/cockpit/data.json"

# (name, workers, priority, duty, period seconds). "rr10 x4 100%" is what the V3 map does today.
LEVELS = [
    ("none", 0, "normal", 1.0, 0.1),
    ("normal x1", 1, "normal", 1.0, 0.1),
    ("normal x4", 4, "normal", 1.0, 0.1),
    ("rr10 x1", 1, "rr10", 1.0, 0.1),
    ("rr10 x2", 2, "rr10", 1.0, 0.1),
    ("rr10 x4 duty25%", 4, "rr10", 0.25, 0.4),
    ("rr10 x4 duty50%", 4, "rr10", 0.5, 0.4),
    ("rr10 x4 duty75%", 4, "rr10", 0.75, 0.4),
    ("rr10 x4 100%", 4, "rr10", 1.0, 0.1),
]


def load_script(workers: int, priority: str, duty: float, period: float, seconds: float) -> str:
    """Python source for busy workers (same loop as dashboard/ivi/ivi.py) that stop by themselves after `seconds`."""
    return (
        "import os, time\n"
        f"WORKERS, DUTY, PERIOD, SECONDS = {int(workers)}, {float(duty)!r}, {float(period)!r}, {float(seconds)!r}\n"
        "def run():\n"
        + ("    os.sched_setscheduler(0, os.SCHED_RR, os.sched_param(10))\n" if priority == "rr10" else "") +
        "    end, value = time.monotonic() + SECONDS, 1\n"
        "    while time.monotonic() < end:\n"
        "        burst = time.monotonic() + DUTY * PERIOD\n"
        "        while time.monotonic() < burst:\n"
        "            for _ in range(2000):\n"
        "                value = (value * 1103515245 + 12345) & 0xFFFFFFFF\n"
        "        if DUTY < 1.0:\n"
        "            time.sleep(PERIOD * (1.0 - DUTY))\n"
        "pids = []\n"
        "for _ in range(WORKERS):\n"
        "    pid = os.fork()\n"
        "    if pid == 0:\n"
        "        run()\n"
        "        os._exit(0)\n"
        "    pids.append(pid)\n"
        "for pid in pids:\n"
        "    os.waitpid(pid, 0)\n"
    )


def classify(row: dict, screen_ok: float) -> str:
    """light (under the limit), moderate (late but talking), severe (seconds late), lost (silent or no cockpit feed)."""
    p99 = row.get("lat_p99_ms")
    if (p99 is None or row.get("n_pairs", 0) < 50 or row.get("skip_ratio", 0.0) > 0.2
            or (row.get("max_gap_ms") or 0.0) > 2000.0 or screen_ok < 0.5):
        return "lost"
    if p99 >= SEVERE_MS:
        return "severe"
    return "moderate" if p99 >= LATENCY_LIMIT_MS else "light"


def start_load(pi2, level, seconds):
    name, workers, priority, duty, period = level
    if workers == 0:
        return
    script = load_script(workers, priority, duty, period, seconds)
    pi2.run(f"sudo systemd-run --quiet --collect --unit sweep-load -p RuntimeMaxSec={int(seconds) + 15} "
            f"/usr/bin/python3 -c {shlex.quote(script)}")


def stop_load(pi2):
    pi2.run("sudo systemctl stop sweep-load.service 2>/dev/null; true", check=False, timeout=60)


def cockpit_feed_ok(pi2) -> bool:
    try:
        done = pi2.run(f"cat {COCKPIT_FEED}", check=False, timeout=12)
        return done.returncode == 0 and json.loads(done.stdout).get("pi3") is not None
    except Exception:
        return False


def measure(boards, level, seconds, margin=6.0, log=print):
    pi2, pi3 = boards["pi2"], boards["pi3"]
    samples, running = [], {"now": True}

    def poll():
        while running["now"]:
            samples.append(cockpit_feed_ok(pi2))
            time.sleep(1.5)

    start_load(pi2, level, seconds)
    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        text = pi3.run(f"timeout {int(seconds)} candump -L -tA can0 || true", check=False, timeout=int(seconds) + 60).stdout
    finally:
        running["now"] = False
        stop_load(pi2)
    thread.join(timeout=15)
    frames = [f for f in (trace.parse_candump_line(line) for line in text.splitlines()) if f is not None]
    result = trace.pair_frames(frames)
    ok = sum(samples[2:]) / max(1, len(samples) - 2) if len(samples) > 3 else 0.0   # first polls: load ramping up
    if not result.pairs:
        return {"name": level[0], "n_pairs": 0, "n_sources": 0, "screen_ok": round(ok, 2), "verdict": "lost"}
    times = [p.t_source for p in result.pairs]
    row = analyze_phases.phase_report(result, [{"name": level[0], "load": level[1], "t0": min(times), "t1": max(times)}],
                                      margin=margin)[0]
    row["screen_ok"] = round(ok, 2)
    row["verdict"] = classify(row, ok)
    log(f"[sweep] {row['name']:<18} p50 {row['lat_p50_ms']} p99 {row['lat_p99_ms']} max {row['lat_max_ms']} ms  "
        f"gap {row['max_gap_ms']} ms  skip {row['skip_ratio']}  screen {row['screen_ok']}  -> {row['verdict']}")
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--seconds", type=int, default=40)
    parser.add_argument("--rest", type=int, default=10, help="seconds between levels, load stopped")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", help="comma separated level names (default: all)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    config, boards = field.load(args.config)
    wanted = {n.strip() for n in args.only.split(",")} if args.only else None
    levels = [lv for lv in LEVELS if wanted is None or lv[0] in wanted]
    hz, cycle = int(config.get("profile_hz", field.DEFAULT_PROFILE_HZ)), config.get("profile_cycle", "synthetic")
    rows = []
    try:
        field.start_real_services(boards, print, hz=hz, cycle=cycle)
        field.start_screen(boards, field.pi3_address(boards["pi3"]), print, virtual=True, labels=field.version_labels(config))
        time.sleep(25)       # let the cockpit and the monitor settle before the first measurement
        for level in levels:
            rows.append(measure(boards, level, args.seconds))
            args.output.write_text(json.dumps(rows, indent=1))
            time.sleep(args.rest)
    finally:
        stop_load(boards["pi2"])
        field.stop_real_services(boards)
    print(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
