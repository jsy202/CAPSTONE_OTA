"""Read-only check that the three boards and the laptop are ready for a field run or a recording.

    python -m bench.preflight .demo/field-config.json

Changes nothing on any board (no restarts, no files written, no slot checks that would overwrite the progress
record). Prints one line per check and exits 1 if any failed, so a recording is not started on a bad setup."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path

from bench import incident_field as field

ROOT = Path(__file__).resolve().parents[1]
CODE_FILES_COMMAND = (f"{field.PY} -c \"import capstone_ota,hashlib,pathlib;r=pathlib.Path(capstone_ota.__file__).parent;"
                      "print('\\n'.join(str(p.relative_to(r))+' '+hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(r.rglob('*.py'))))\"")


def code_files(package=ROOT / "capstone_ota") -> dict:
    """{relative path: sha256} of the package's source: the same fingerprint the boards compute of their installed copy."""
    package = Path(package)
    return {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.rglob("*.py"))}


def compare_code(local: dict, remote_output: str):
    """Every local file must be on the board with the same content. Extra files there (an older install's leftovers) are
    only reported: nothing in this checkout imports them."""
    remote = dict(line.split() for line in remote_output.splitlines() if len(line.split()) == 2)
    wrong = sorted(name for name, digest in local.items() if remote.get(name) != digest)
    extra = sorted(set(remote) - set(local))
    note = ("same" if not wrong else "differs: " + ", ".join(wrong[:5]) + " (run `incident_field setup`)")
    return not wrong, note + (f"; leftovers on the board: {', '.join(extra)}" if extra else "")


def laptop_addresses() -> list[str]:
    done = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=10)
    return [line.split()[3].split("/")[0] for line in done.stdout.splitlines() if len(line.split()) > 3]


def _safe(name, test):
    try:
        ok, detail = test()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError, socket.error) as exc:
        ok, detail = False, f"{type(exc).__name__}: {' '.join(str(exc).split())[:110]}"
    return name, bool(ok), str(detail)


def check(config, boards, local_files=None, laptop_addresses=laptop_addresses):
    """[(name, ok, detail)] for everything a run needs. One failing check never hides the others."""
    local_files = local_files if local_files is not None else code_files()
    addresses = laptop_addresses() if callable(laptop_addresses) else laptop_addresses
    wanted = field.baseline_version(config)
    results = []
    for name in ("pi1", "pi2", "pi3"):
        results.append(_safe(f"{name.capitalize()} reachable", lambda b=boards[name]: ("ok" in b.run("echo ok", timeout=20).stdout, "")))
    for name in ("pi1", "pi2", "pi3"):
        results.append(_safe(f"{name.capitalize()} CAN bus up", lambda b=boards[name]: (
            lambda out: ("UP" in out, out.strip()))(b.run("ip -br link show can0", timeout=20).stdout)))

    def status():
        current = field.pi2_status(boards["pi2"])
        return current.get("version") == wanted and current.get("phase") == "stable", \
            f"version={current.get('version')} phase={current.get('phase')} slot={current.get('active_slot')}"
    results.append(_safe(f"Pi2 IVI stable at {wanted}", status))
    results.append(_safe("Pi2 runs this checkout's code",
                         lambda: compare_code(local_files, boards["pi2"].run(CODE_FILES_COMMAND, timeout=60).stdout)))
    results.append(_safe(f"laptop has laptop_ip {config.get('laptop_ip')}", lambda: (config.get("laptop_ip") in addresses, ", ".join(addresses[:4]))))
    release = Path(config.get("release_dir", "/nonexistent"))
    needed = [release / "keys/ivi-private.pem", release / "releases/v2", release / "releases/v3"]
    results.append(_safe("release_dir has the signing key and releases v2, v3",
                         lambda: (all(p.exists() for p in needed), ", ".join(p.name for p in needed if not p.exists()) or "ok")))
    pace = config.get("demo_pace") or {}
    results.append(_safe("demo pace set (download/install/park)", lambda: (all(k in pace for k in ("download", "install", "park")), json.dumps(pace))))
    return results


def exit_code(results) -> int:
    return 0 if all(ok for _n, ok, _d in results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    config, boards = field.load(args.config)
    results = check(config, boards)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print("READY" if exit_code(results) == 0 else "NOT READY")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
