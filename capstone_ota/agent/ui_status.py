"""Read-only Cluster UI status derived from the application A/B slot journal.

The status is display metadata for the Qt Digital Cluster only. It never
writes slot directories or state.json and is not consulted by any OTA
decision. The trial rule mirrors ZonalAgent.publish_heartbeat: the trial
application runs when trial_slot is set and is the selected slot. State names
are the lowercase ApplicationState members plus "unknown"; anything that
cannot be established fails closed to "unknown", never to a guessed version.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from capstone_ota.common.can_protocol import ApplicationState
from capstone_ota.common.errors import OtaError

from .slots import SlotState


SCHEMA_VERSION = 1
MAX_STATUS_BYTES = 4096
_KEYS = {"schema_version", "state", "version", "restored_at"}
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
_SELECTORS = {"slots/A": "A", "slots/B": "B"}
_STABLE = ApplicationState.STABLE.name.lower()
_TRIAL = ApplicationState.TRIAL.name.lower()


@dataclass(frozen=True)
class SlotSnapshot:
    state: SlotState | None
    selected_slot: str | None


def read_snapshot(install_root: Path) -> SlotSnapshot:
    """Read the journal and selector independently; failures become None."""
    root = Path(install_root)
    try:
        state = SlotState.load(root / "state.json")
    except OtaError:
        state = None
    try:
        selected = _SELECTORS.get(os.readlink(root / "active-slot"))
        if selected is not None and not (root / "slots" / selected).is_dir():
            selected = None
    except OSError:
        selected = None
    return SlotSnapshot(state, selected)


def _status(state: str, version: str | None, restored_at: float | None = None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "state": state, "version": version, "restored_at": restored_at}


def decide(snapshot: SlotSnapshot, initial_version: str, previous: dict | None, now: float) -> dict:
    state, selected = snapshot.state, snapshot.selected_slot
    if state is None or selected is None:
        return _status("unknown", None)
    if state.trial_slot is not None and selected == state.trial_slot:
        name, version = _TRIAL, state.trial_version
    elif selected == state.stable_slot:
        name, version = _STABLE, state.stable_version or initial_version
    else:
        return _status("unknown", None)
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        return _status("unknown", None)
    return _status(name, version, _restored_at(name, version, previous, now))


def _restored_at(name: str, version: str, previous: dict | None, now: float) -> float | None:
    """Mark only the visible trial -> previous-version transition (rollback).

    Commit keeps the trial version and an abort before activation never had a
    trial on screen, so neither sets it. The mark persists while the same
    stable version stays selected; the UI decides how long to show it.
    """
    if name != _STABLE or not previous:
        return None
    if previous.get("state") == _TRIAL and previous.get("version") != version:
        return float(now)
    carried = previous.get("restored_at")
    if (previous.get("state") == _STABLE and previous.get("version") == version
            and type(carried) is float and math.isfinite(carried) and carried >= 0):
        return carried
    return None


def _encode(status: dict) -> bytes:
    return (json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_regular(path: Path, limit: int) -> bytes | None:
    """Read at most limit+1 bytes of a regular file without following links.

    The output directory belongs to another account; a planted symlink, FIFO
    or device must never be followed, block, or be read without bound.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)


def read_previous(path: Path) -> dict | None:
    """Return the last published v1 status, or None when absent or invalid."""
    raw = _read_regular(Path(path), MAX_STATUS_BYTES)
    if raw is None or len(raw) > MAX_STATUS_BYTES:
        return None
    try:
        status = json.loads(raw)
    except ValueError:
        return None
    if (not isinstance(status, dict) or set(status) != _KEYS
            or type(status["schema_version"]) is not int or status["schema_version"] != SCHEMA_VERSION):
        return None
    return status


def write_status(path: Path, status: dict) -> bool:
    """Atomically publish status (0644); False when content is unchanged.

    The output directory is owned by tmpfiles and is never created here.
    """
    path = Path(path)
    data = _encode(status)
    if _read_regular(path, MAX_STATUS_BYTES) == data:
        return False
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def run_once(install_root: Path, initial_version: str, output: Path,
             now: Callable[[], float] = time.time) -> dict:
    status = decide(read_snapshot(install_root), initial_version, read_previous(output), now())
    write_status(output, status)
    return status


def watch(install_root: Path, initial_version: str, output: Path, interval: float, *,
          iterations: int | None = None, sleep: Callable[[float], None] = time.sleep,
          now: Callable[[], float] = time.time) -> None:
    """Poll until stopped; a failed iteration publishes unknown and continues."""
    count = 0
    while iterations is None or count < iterations:
        count += 1
        try:
            run_once(install_root, initial_version, output, now)
        except Exception as exc:
            print(f"capstone-ota-ui-status: {exc}", file=sys.stderr)
            try:
                write_status(output, _status("unknown", None))
            except Exception:
                _discard(output)
        sleep(interval)


def _discard(output: Path) -> None:
    """Never leave a stale state on screen: no file means unknown in the UI."""
    try:
        Path(output).unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish read-only Cluster UI status")
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--initial-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.watch:
        if args.interval <= 0:
            parser.error("--interval must be positive")
        watch(args.install_root, args.initial_version, args.output, args.interval)
        return 0
    try:
        run_once(args.install_root, args.initial_version, args.output)
    except Exception as exc:
        # Snapshot runs as ExecStartPre; it must never block the application.
        print(f"capstone-ota-ui-status: cannot publish {args.output}: {exc}", file=sys.stderr)
        _discard(args.output)
    return 0
