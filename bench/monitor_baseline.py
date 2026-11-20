"""Re-measure the Pi3 monitor baseline at the drive profile's real frame rate (healthy cluster, no load).

    python -m bench.monitor_baseline CONFIG --hz 50 --seconds 120 --output .demo/baseline-50hz.json [--install]

The monitor's freeze/jitter rules compare against a baseline measured at the same rate, so raising the
CAN rate needs a new baseline. This records a candump on Pi3 while Pi1 plays the profile at --hz, fits
the baseline with the monitor's own fit_baseline, and (with --install) puts it on Pi3 next to the old one
(it never overwrites the old file). Run it only while the cluster is idle/healthy: whatever happens during
the recording becomes "normal".
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from bench import incident_field as field
from capstone_ota.monitor import trace
from capstone_ota.monitor.metrics import fit_baseline



def baseline_from_candump(text: str) -> dict:
    frames = [f for f in (trace.parse_candump_line(line) for line in text.splitlines()) if f is not None]
    result = trace.pair_frames(frames)
    if not result.pairs:
        raise ValueError("no vehicle/echo pairs in the recording: is the cluster running and the profile playing?")
    return fit_baseline(result)._asdict()


def record(pi1, pi3, hz: int, seconds: int, log=print, cycle: str = "synthetic") -> str:
    pi1.run("pkill -f '[p]lay_profile'; true", check=False)
    pi1.run(f"PYTHONPATH={field.RUNTIME} nohup setsid {field.PY} -m bench.play_profile --channel can0 --hz {hz} "
            f"--cycle {cycle} > {field.RUNTIME}/profile.log 2>&1 < /dev/null & echo started")
    log(f"[baseline] profile at {hz} Hz, recording {seconds} s on Pi3")
    done = pi3.run(f"timeout {seconds} candump -L -tA can0 || true", check=False, timeout=seconds + 60)
    return done.stdout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--hz", type=int, required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycle", default="synthetic", choices=["synthetic", "demo"])
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    config = json.loads(args.config.read_text())
    pi1, pi3 = field.Board(config["pi1_ssh"]), field.Board(config["pi3_ssh"])
    text = record(pi1, pi3, args.hz, args.seconds, cycle=args.cycle)
    baseline = baseline_from_candump(text)
    args.output.write_text(json.dumps(baseline, indent=1))
    print(json.dumps(baseline))
    if args.install:
        remote = field.monitor_baseline_for(args.hz, args.cycle)
        pi3.write(remote, json.dumps(baseline), "644")
        print("installed", remote, "(start the monitor with --baseline", shlex.quote(remote) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
