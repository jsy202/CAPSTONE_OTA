"""One device, two application directories, one durable state journal.

Read update() top to bottom: recover, download/verify, stage, trial, commit.
An interrupted trial always returns to the recorded committed application.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from .bundle import MAX_ARCHIVE, app_check, extract, metadata_check, tree_hash, unique_json, verify, version_key

SERVICE = "capstone-central.service"


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def fetch(url: str, target: Path, limit: int) -> bytes:
    started, size = time.monotonic(), 0
    digest = hashlib.sha256()
    with urlopen(url, timeout=5) as source, target.open("xb") as output:
        while chunk := source.read(65536):
            size += len(chunk)
            if size > limit or time.monotonic() - started > 30:
                raise ValueError("download exceeds size/time limit")
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    return digest.digest()


class Device:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    @contextmanager
    def _locked(self):
        if self.root.is_symlink():
            raise ValueError("device root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("device is busy with another update") from exc
            yield
        finally:
            os.close(fd)

    def _load(self) -> dict:
        if self.root.is_symlink():
            raise ValueError("device root must be a real directory")
        path = self.root / "state.json"
        if not path.exists() and not path.is_symlink():
            if any((self.root / name).exists() or (self.root / name).is_symlink()
                   for name in ("current", "slots/A", "slots/B")):
                raise ValueError("state is missing on an existing device; refusing reinitialization")
            return {"phase": "stable", "committed": None, "pending": None, "min_svn": 0,
                    "systemd": False, "result": "empty"}
        try:
            if path.is_symlink() or path.stat().st_size > 65536:
                raise ValueError("invalid journal file")
            state = unique_json(path.read_bytes())
            if (not isinstance(state, dict)
                    or set(state) != {"phase", "committed", "pending", "min_svn", "systemd", "result"}
                    or state["phase"] not in {"stable", "trial"}
                    or type(state["min_svn"]) is not int or not 0 <= state["min_svn"] <= 2**31 - 1
                    or type(state["systemd"]) is not bool
                    or state["result"] not in {"empty", "trial", "committed", "rolled_back", "recovery_failed", "failed"}):
                raise ValueError("invalid journal fields")
            for name in ("committed", "pending"):
                item = state[name]
                if item is None:
                    continue
                if not isinstance(item, dict) or set(item) != {"slot", "release", "tree_sha256"}:
                    raise ValueError("invalid slot record")
                if item["slot"] not in {"A", "B"}:
                    raise ValueError("invalid slot")
                metadata_check(item["release"])
                value = item["tree_sha256"]
                if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise ValueError("invalid tree hash")
            committed, pending = state["committed"], state["pending"]
            if ((state["phase"] == "trial") != (pending is not None)
                    or (committed and committed["release"]["svn"] < state["min_svn"])
                    or (not committed and state["min_svn"] != 0)
                    or (committed and pending and committed["slot"] == pending["slot"])):
                raise ValueError("inconsistent slot state")
            return state
        except (OSError, TypeError, KeyError, ValueError) as exc:
            raise ValueError("invalid device state; repair from trusted records, do not reset the floor") from exc

    def _save(self, state: dict) -> None:
        atomic_write(self.root / "state.json", json.dumps(state, sort_keys=True).encode())

    def _event(self, event: str) -> None:
        fd = os.open(self.root / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as log:
            log.write(json.dumps({"time": time.time(), "event": event}) + "\n")

    def _slot(self, name: str) -> Path:
        if name not in {"A", "B"}:
            raise ValueError("invalid slot name")
        slots = self.root / "slots"
        target = slots / name
        if slots.is_symlink() or target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError("slot must be a real directory")
        return target

    def _selected(self) -> str | None:
        current = self.root / "current"
        if not current.is_symlink():
            if current.exists():
                raise ValueError("current must be a slot selector symlink")
            return None
        target = os.readlink(current)
        if target not in {"slots/A", "slots/B"}:
            raise ValueError("current selector escapes the slots")
        return target[-1]

    def status(self) -> dict:
        state = self._load()
        committed = state["committed"]
        return {"phase": state["phase"], "active_slot": self._selected(),
                "version": committed["release"]["version"] if committed else None,
                "min_svn": state["min_svn"], "result": state["result"],
                "trial_version": state["pending"]["release"]["version"] if state["pending"] else None}

    def _select(self, slot: str | None) -> None:
        self._selected()  # Never replace an unexpected regular file or selector.
        current = self.root / "current"
        if slot is None:
            current.unlink(missing_ok=True)
        else:
            self._slot(slot)
            temporary = self.root / (".current-" + uuid4().hex)
            try:
                temporary.symlink_to("slots/" + slot)
                os.replace(temporary, current)
            finally:
                temporary.unlink(missing_ok=True)
        sync_directory(self.root)

    def _service(self, action: str) -> bool:
        return subprocess.run(["systemctl", action, SERVICE], timeout=10,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def _check(self, slot: str) -> bool:
        path = self._slot(slot)
        app = app_check(path)
        # Check uses the application's normal initialization; it must be read-only.
        process = subprocess.Popen([str(app), "--check"], cwd=path, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return process.wait(timeout=5) == 0
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return False

    def _activate(self, slot: str, state: dict) -> None:
        if state["systemd"] and not self._service("stop"):
            raise ValueError("cannot stop Central service")
        self._select(slot)
        if not self._check(slot):
            raise ValueError("application initialization check failed")
        if state["systemd"]:
            if not self._service("restart"):
                raise ValueError("cannot restart Central service")
            for _ in range(5):
                time.sleep(0.2)
                if not self._service("is-active"):
                    raise ValueError("Central service did not stay active")

    def _verify_record(self, record: dict, floor: int) -> None:
        slot = self._slot(record["slot"])
        if record["release"]["svn"] < floor:
            raise ValueError("recovery security version is below the floor")
        if tree_hash(slot) != record["tree_sha256"]:
            raise ValueError("recovery application tree hash mismatch")

    def _recover(self, state: dict) -> dict:
        if state["phase"] != "trial":
            committed = state["committed"]
            if committed:
                self._verify_record(committed, state["min_svn"])
                if self._selected() != committed["slot"]:
                    raise ValueError("state and current selector disagree")
            return state
        self._event("recover")
        try:
            previous = state["committed"]
            if previous:
                self._verify_record(previous, state["min_svn"])
                self._activate(previous["slot"], state)
            else:
                self._select(None)
            state.update(phase="stable", pending=None, result="rolled_back" if previous else "failed")
            self._save(state)
            return state
        except Exception as exc:
            state["result"] = "recovery_failed"
            self._save(state)
            self._event("recovery_failed")
            raise ValueError("recovery failed; no successful recovery is claimed") from exc

    def update(self, url: str | None = None, public_key: Path | None = None, systemd: bool = False) -> dict:
        with self._locked():
            state = self._recover(self._load())
            if url is None:
                return self.status()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
                raise ValueError("use an HTTP(S) release directory URL without credentials/query")
            pinned = self.root / "trusted.pub"
            if pinned.is_symlink():
                raise ValueError("pinned key must be a regular file")
            if pinned.exists():
                if public_key is not None and Path(public_key).read_bytes() != pinned.read_bytes():
                    raise ValueError("provided key differs from the pinned key")
                public_key = pinned
            elif state["committed"] or public_key is None:
                raise ValueError("first install requires --public-key; initialized devices need their pinned key")
            if systemd and state["committed"] and not state["systemd"]:
                raise ValueError("choose --systemd at the first install, not midway through migration")
            self._event("download_and_verify")
            with tempfile.TemporaryDirectory(prefix=".download-", dir=self.root) as directory:
                staging = Path(directory)
                base = url.rstrip("/") + "/"
                fetch(base + "release.json", staging / "release.json", 16384)
                fetch(base + "release.sig", staging / "release.sig", 64)
                raw, signature = (staging / "release.json").read_bytes(), (staging / "release.sig").read_bytes()
                release = verify(raw, signature, public_key)
                if release["svn"] < state["min_svn"]:
                    raise ValueError("release security version is below the committed floor")
                previous = state["committed"]
                if previous and version_key(release["version"]) <= version_key(previous["release"]["version"]):
                    raise ValueError("release product version must be newer")
                downloaded_hash = fetch(base + "app.tar.gz", staging / "app.tar.gz", MAX_ARCHIVE).hex()
                if downloaded_hash != release["sha256"]:
                    raise ValueError("downloaded archive hash mismatch")
                extract(staging / "app.tar.gz", staging / "payload")
                slot = "B" if previous and previous["slot"] == "A" else "A"
                target = self._slot(slot)
                # Establish the empty journal/key before touching a first slot:
                # a first-install crash is retryable, never mistaken for lost state.
                if not pinned.exists():
                    atomic_write(pinned, Path(public_key).read_bytes())
                state["systemd"] = state["systemd"] or systemd
                if not (self.root / "state.json").exists():
                    if state["systemd"]:
                        self.root.chmod(0o755)
                    self._save(state)
                target.parent.mkdir(exist_ok=True)
                target.parent.chmod(0o755)
                if target.exists():
                    shutil.rmtree(target)  # Exact validated INACTIVE slot only.
                os.replace(staging / "payload", target)
                for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if path.is_dir():
                        sync_directory(path)
                sync_directory(target)
                sync_directory(target.parent)
                state.update(phase="trial", result="trial",
                             pending={"slot": slot, "release": release, "tree_sha256": tree_hash(target)})
                self._save(state)  # Intent durable BEFORE switching current.
            try:
                self._event("trial")
                self._activate(slot, state)
                if tree_hash(target) != state["pending"]["tree_sha256"]:
                    raise ValueError("application modified its own slot during the check")
                committed = dict(state, phase="stable", committed=state["pending"], pending=None,
                                 min_svn=max(state["min_svn"], release["svn"]), result="committed")
                self._save(committed)  # Release and floor become committed together.
            except Exception:
                # replace() may have committed state before a later fsync/log error.
                # The durable journal, not our stale in-memory trial, is authoritative.
                self._recover(self._load())
                return self.status()
            self._event("committed")
            return self.status()
