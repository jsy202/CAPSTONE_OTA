"""Independent virtual test oracle. Its policy is outside the updated payload."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

if __package__:
    from .wire import decode
else:
    from wire import decode

HERE = Path(__file__).resolve().parent
BEFORE = [400, 410, 420, 430, 440, 450, 460, 470]
AFTER = [600, 610, 620, 630, 640, 650]


def exchange(command, rows, timeout=2, *, allow_negative_verdict=False):
    """Bound input/output/time and reap the whole actor group on failure."""
    payload = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    if len(payload) > 4096:
        raise ValueError("actor input too large")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    buffers = {"out": bytearray(), "err": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write(payload)
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "out")
            selector.register(process.stderr, selectors.EVENT_READ, "err")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("actor timeout")
                for key, _event in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    buffers[key.data].extend(chunk)
                    if len(buffers[key.data]) > 65536:
                        raise ValueError("actor output too large")
        try:
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ValueError("actor timeout") from exc
        output = [json.loads(line) for line in buffers["out"].decode().splitlines()]
        negative = (allow_negative_verdict and process.returncode == 1 and len(output) == 1
                    and isinstance(output[0], dict) and output[0].get("passed") is False)
        if process.returncode != 0 and not negative:
            raise ValueError("actor exited unsuccessfully")
        return output, process.pid
    finally:
        # Descendants may retain pipes even after the original actor has exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()


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
