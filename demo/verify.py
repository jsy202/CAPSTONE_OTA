"""Independent virtual test oracle. Its policy is outside the updated payload."""
import json
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capstone_ota.process import exchange

if __package__:
    from .wire import decode
else:
    from wire import decode

HERE = Path(__file__).resolve().parent
BEFORE = [400, 410, 420, 430, 440, 450, 460, 470]
AFTER = [600, 610, 620, 630, 640, 650]


def verify(slot, timeout=2):
    report = {"passed": False, "transport": "stdio CAN-payload simulation", "initialization_ok": False,
              "frame_format_ok": False, "accepted_before": 0, "accepted_after": 0,
              "post_counters": [], "last_model_speed": None, "actor_pids": {"verifier": os.getpid()},
              "error": None}
    try:
        app = str(Path(slot).resolve() / "bin/app")
        initialized, _pid = exchange([app, "--check"], [], timeout)
        if len(initialized) != 1 or initialized[0].get("initialized") is not True:
            raise ValueError("initialization response invalid")
        report["initialization_ok"] = True
        inputs = ([{"op": "sample", "speed": speed} for speed in BEFORE] + [{"op": "reconnect"}]
                  + [{"op": "sample", "speed": speed} for speed in AFTER])
        output, pid = exchange([app], inputs, timeout)
        report["actor_pids"]["central"] = pid
        if len(output) != 15 or output[8] != {"event": "reconnected"}:
            raise ValueError("Central response sequence invalid")
        frames = output[:8] + output[9:]
        decoded = [decode(frame) for frame in frames]
        report["frame_format_ok"] = True
        report["post_counters"] = [row["counter"] for row in decoded[8:]]
        models, pid = exchange([sys.executable, str(HERE / "cluster.py")], frames, timeout)
        report["actor_pids"]["cluster"] = pid
        if len(models) != 14 or any(not isinstance(row, dict) or set(row) != {"accepted", "counter", "speed"}
                                    or type(row["accepted"]) is not bool for row in models):
            raise ValueError("Cluster response invalid")
        report["accepted_before"] = sum(row["accepted"] for row in models[:8])
        report["accepted_after"] = sum(row["accepted"] for row in models[8:])
        report["last_model_speed"] = models[-1]["speed"]
        if not all(row["accepted"] and row["speed"] == speed for row, speed in zip(models[:8], BEFORE)):
            raise ValueError("pre-reconnect processing failed")
        report["passed"] = all(row["accepted"] and row["speed"] == speed
                               for row, speed in zip(models[8:], AFTER))
        if not report["passed"]:
            report["error"] = "RECONNECT_SEQUENCE_INCOMPATIBLE"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        report["error"] = str(exc)
    return report


if __name__ == "__main__":
    result = verify(Path(sys.argv[1]))
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
