"""Bounded JSON-line subprocess communication, shared by OTA control and benches."""
import json
import os
import selectors
import signal
import subprocess
import time


def exchange(command, rows, timeout=2, *, allow_negative_verdict=False, allow_rollback=False):
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
        single = len(output) == 1 and isinstance(output[0], dict)
        negative = (allow_negative_verdict and process.returncode == 1 and single
                    and output[0].get("passed") is False)
        rolled_back = (allow_rollback and process.returncode == 2 and single
                       and output[0].get("result") in {"rolled_back", "failed"})
        if process.returncode != 0 and not negative and not rolled_back:
            raise ValueError("actor exited unsuccessfully")
        return output, process.pid
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
