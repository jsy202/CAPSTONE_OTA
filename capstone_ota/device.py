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
DOWNLOAD_TIMEOUT = 30


class DownloadDeadline(TimeoutError):
    """Must propagate even if SIGALRM interrupts optional display telemetry."""


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


def fetch(url: str, target: Path, limit: int, progress=None) -> bytes:
    started, size = time.monotonic(), 0
    digest = hashlib.sha256()
    def expired(_number, _frame):
        raise DownloadDeadline("download deadline exceeded")
    # Linux main-thread CLI: a socket timeout alone cannot stop byte trickles.
    # The timer bounds headers AND body without adding a downloader service.
    previous_handler = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, DOWNLOAD_TIMEOUT)
    try:
        with urlopen(url, timeout=5) as source, target.open("xb") as output:
            try:
                total = int(source.headers.get("Content-Length", "0"))
            except ValueError:
                total = 0
            total = total if 0 < total <= limit else None
            if progress:
                progress(0, total)
            while chunk := source.read(65536):
                size += len(chunk)
                if size > limit:
                    raise ValueError("download exceeds size limit")
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(size, total if total is None or size <= total else None)
            output.flush()
            os.fsync(output.fileno())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])
    return digest.digest()


MAX_PACE_SECONDS = 120.0


def pace(progress, seconds: float, clock=time.monotonic, sleep=time.sleep):
    """Demo pacing: wrap progress(done, total) so the whole stage takes about `seconds`.

    The wait happens BEFORE the report, so the bar shows bytes that really were read/written by then. Without a
    known total or a time it changes nothing."""
    state = {"start": None}

    def paced(done, total):
        if state["start"] is None:
            state["start"] = clock()
        if total and seconds > 0:
            wait = state["start"] + seconds * min(done / total, 1.0) - clock()
            if wait > 0:
                sleep(wait)
        progress(done, total)
    return paced


def load_pace(root: Path) -> dict:
    """Optional demo file `<device root>/demo-pace.json` {"download": s, "install": s}: slow those two stages down so a
    viewer can watch them. Off when the file is missing; bad values are ignored. It only ever adds delay."""
    try:
        data = json.loads((Path(root) / "demo-pace.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: float(data[key]) for key in ("download", "install")
            if type(data.get(key)) in (int, float) and 0 < data[key] <= MAX_PACE_SECONDS}


class Device:
    def __init__(self, root: Path, recovery_verification: bool = True):
        self.root = Path(root).absolute()
        self._progress_data = {}
        # Demo-only kill switch for the pinned recovery check below; never flip this from
        # network input, and never default it off. It exists so a defended-vs-vulnerable
        # demo can show the same tampered recovery slot being blocked, then not blocked.
        self.recovery_verification = recovery_verification

    def _progress(self, stage: str, **fields) -> None:
        """Best-effort display telemetry, never an input to security decisions."""
        if "work" not in fields and stage not in ("install", "recover"):
            self._progress_data.pop("work", None)   # a finished install/rollback does not keep reporting
        self._progress_data.update(fields, stage=stage, updated=time.time(), pid=os.getpid())
        try:
            # Preserve brief transitions between display polls; telemetry never authorizes execution.
            self._progress_data.setdefault('timeline_id', str(time.time_ns()))
            timeline = self._progress_data.setdefault('timeline', [])
            if not timeline or timeline[-1]['stage'] != stage:
                timeline.append({key: self._progress_data.get(key)
                                 for key in ('stage', 'version', 'candidate', 'updated')})
            previous = self._progress_data.get('slot_view', {})
            view = {name: {'version': previous.get(name, {}).get('version'), 'role': 'retained'}
                    for name in ('A', 'B')}
            try:
                state = self._load()
                view.update(selected=self._selected(), available=True)
                for key, role in [('committed', 'committed'), ('pending', 'trial')]:
                    record = state[key]
                    if record:
                        view[record['slot']] = {'version': record['release']['version'], 'role': role}
                if stage == 'recovery_failed' and state['committed']:
                    view[state['committed']['slot']]['role'] = 'recovery_failed'
            except DownloadDeadline:
                raise
            except (OSError, ValueError):
                view = {name: {'version': None, 'role': 'unknown'} for name in ('A', 'B')}
                view.update(selected=None, available=False)
            self._progress_data['slot_view'] = view
            atomic_write(self.root / "progress.json", json.dumps(self._progress_data).encode())
        except DownloadDeadline:
            raise
        except OSError:
            pass

    def _work_progress(self, stage: str, kind: str):
        """Display-only callback: how much of an install/rollback is done, written at most every 50 ms (and at the end)."""
        last = {"at": 0.0}

        def report(done: int, total: int) -> None:
            now = time.monotonic()
            if done < total and now - last["at"] < 0.05:
                return
            last["at"] = now
            self._progress(stage, work={"kind": kind, "done": done, "total": total})
        seconds = load_pace(self.root).get("install") if kind == "install" else None
        return pace(report, seconds) if seconds else report

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
            try:
                yield
            except Exception as exc:
                if self._progress_data.get("stage") != "recovery_failed":
                    self._progress("error", detail=str(exc))
                raise
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
                    or (committed and committed["release"]["svn"] != state["min_svn"])
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
        self._progress(event)

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

    def recovery_evidence(self) -> dict:
        """What the last recovery check compared: the committed tree hash and the one found on disk.

        Pi2 reports this in its own answer so a remote gateway never has to read Pi2's files."""
        return {"expected_tree_sha256": self._progress_data.get("expected_tree_sha256"),
                "observed_tree_sha256": self._progress_data.get("observed_tree_sha256"),
                "checked_slot": self._progress_data.get("checked_slot")}

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
        process = subprocess.Popen(self._check_arguments(app), cwd=path, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return process.wait(timeout=5) == 0
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return False

    def _check_arguments(self, app: Path) -> list[str]:
        return [str(app), "--check"]

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

    def _verify_record(self, record: dict, floor: int, progress=None) -> None:
        slot = self._slot(record["slot"])
        if not self.recovery_verification:
            self._event("VULNERABLE_MODE_RECOVERY_UNVERIFIED")
            return
        if record["release"]["svn"] < floor:
            raise ValueError("recovery security version is below the floor")
        try:
            observed = tree_hash(slot, progress)
        except OSError:
            observed = None
        # Display/incident telemetry only; the comparison below is the security decision.
        self._progress_data.update(expected_tree_sha256=record["tree_sha256"], observed_tree_sha256=observed,
                                   checked_slot=record["slot"])
        if observed != record["tree_sha256"]:
            raise ValueError("recovery application tree hash mismatch")

    def _recover(self, state: dict) -> dict:
        if state["phase"] != "trial":
            committed = state["committed"]
            if committed:
                self._verify_record(committed, state["min_svn"])
                if self._selected() != committed["slot"]:
                    raise ValueError("state and current selector disagree")
            return state
        self._progress("recover", version=state["committed"]["release"]["version"] if state["committed"] else None,
                       candidate=state["pending"]["release"]["version"])
        self._event("recover")
        try:
            previous = state["committed"]
            if previous:
                self._verify_record(previous, state["min_svn"], self._work_progress("recover", "rollback"))
                self._progress("recovery_verified")
                self._activate(previous["slot"], state)
            else:
                if state["systemd"] and not self._service("stop"):
                    raise ValueError("cannot stop the first-install trial service")
                self._select(None)
            state.update(phase="stable", pending=None, result="rolled_back" if previous else "failed")
            self._save(state)
            self._progress(state["result"])
            return state
        except Exception as exc:
            state["result"] = "recovery_failed"
            self._save(state)
            self._progress("recovery_failed", detail=str(exc))
            self._event("recovery_failed")
            try:
                if state["systemd"] and self._service("stop"):
                    # The candidate just failed its trial and nothing safe can replace it yet: do not leave it
                    # running (it may be what is starving other applications) while an operator decides.
                    self._event("service_stopped_after_recovery_failure")
            except (OSError, subprocess.SubprocessError):
                pass  # Best effort: never hide the recovery failure itself behind a systemctl problem.
            raise ValueError("recovery failed; no successful recovery is claimed") from exc

    def _download_release(self, url: str, public_key: Path, staging: Path, precheck=None) -> dict:
        """Fetch release.json, release.sig and app.tar.gz into `staging`; return the verified release.

        `precheck(release)` runs right after the signature is verified and before the archive
        is downloaded, so a rejected release never costs a full download."""
        base = url.rstrip("/") + "/"
        fetch(base + "release.json", staging / "release.json", 16384)
        fetch(base + "release.sig", staging / "release.sig", 64)
        raw, signature = (staging / "release.json").read_bytes(), (staging / "release.sig").read_bytes()
        self._progress("signature")
        release = verify(raw, signature, public_key)
        self._progress("signature_verified", candidate=release["version"])
        if precheck:
            precheck(release)
        report = lambda received, total: self._progress("download", received=received, total=total)  # noqa: E731
        seconds = load_pace(self.root).get("download")
        downloaded_hash = fetch(base + "app.tar.gz", staging / "app.tar.gz", MAX_ARCHIVE,
                                progress=pace(report, seconds) if seconds else report).hex()
        self._progress("archive_verify")
        if downloaded_hash != release["sha256"]:
            raise ValueError("downloaded archive hash mismatch")
        return release

    def _clear_stale_staging(self) -> None:
        """Remove staging directories a killed run left behind. Callers hold the device lock."""
        for pattern in (".repair-*", ".download-*"):
            for leftover in self.root.glob(pattern):
                if leftover.is_dir() and not leftover.is_symlink():
                    shutil.rmtree(leftover, ignore_errors=True)

    def repair(self, url: str) -> dict:
        """The only exit from recovery_failed: reinstall exactly the committed release.

        Never relaxes update()'s version/svn rules; anything but the committed release, with
        the committed tree hash, is rejected before the committed slot is touched."""
        with self._locked():
            self._progress_data = {}
            state = self._load()
            committed = state["committed"]
            if state["phase"] != "trial" or state["result"] != "recovery_failed" or not committed:
                raise ValueError("repair is only for a recovery_failed device")
            pinned = self.root / "trusted.pub"
            if pinned.is_symlink() or not pinned.is_file():
                raise ValueError("pinned key must be a regular file")
            self._event("repair_started")
            self._clear_stale_staging()
            with tempfile.TemporaryDirectory(prefix=".repair-", dir=self.root) as directory:
                staging = Path(directory)
                try:
                    release = self._download_release(url, pinned, staging)
                    if release != committed["release"]:
                        raise ValueError("repair release is not the committed release")
                    extract(staging / "app.tar.gz", staging / "payload")
                    if tree_hash(staging / "payload") != committed["tree_sha256"]:
                        raise ValueError("repair image tree hash mismatch")
                except Exception:
                    self._event("repair_rejected")
                    raise
                target = self._slot(committed["slot"])
                if target.exists():
                    shutil.rmtree(target)
                os.replace(staging / "payload", target)
                for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    if path.is_dir():
                        sync_directory(path)
                sync_directory(target)
                sync_directory(target.parent)
            self._event("repair_installed")
            self._recover(state)  # Same verification, activation and external re-check as any rollback.
            return self.status()

    def update(self, url: str | None = None, public_key: Path | None = None, systemd: bool = False) -> dict:
        with self._locked():
            self._progress_data = {}
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
            self._progress("metadata", version=state["committed"]["release"]["version"] if state["committed"] else None,
                           candidate=None, mismatch=None, detail="")
            with tempfile.TemporaryDirectory(prefix=".download-", dir=self.root) as directory:
                staging = Path(directory)

                def precheck(release):
                    if release["svn"] < state["min_svn"]:
                        raise ValueError("release security version is below the committed floor")
                    if state["committed"] and version_key(release["version"]) <= version_key(state["committed"]["release"]["version"]):
                        raise ValueError("release product version must be newer")

                release = self._download_release(url, public_key, staging, precheck)
                previous = state["committed"]
                self._progress("install")
                extract(staging / "app.tar.gz", staging / "payload", self._work_progress("install", "install"))
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
            self._progress("committed", version=release["version"])
            return self.status()
