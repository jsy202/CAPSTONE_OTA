# Independent native Qt OTA progress panel

The panel is a separate Qt Quick process outside application A/B slots. Replacing
or terminating the candidate dashboard does not terminate the panel. It only reads
`DEVICE_ROOT/progress.json`; it cannot authorize an update or alter its verdict.
No new Python package, HTTP endpoint, system service or global environment setting
is required.

The updater records measured archive bytes and HTTP Content-Length. The panel
shows a percentage only during download when those values are usable. Signature
checks, extraction, trials, commit and rollback show actual stages. Missing or
malformed records show a waiting state; a nonterminal record whose updater exited,
is over 65 seconds old, or has a future timestamp shows an unconfirmed result.
Completed records remain available after the updater exits.

The first failed verifier observation remains visible through recovery: in the PC
mixed scenario it records expected **62** and actual QML gauge **45 km/h**. A new
update clears that observation. Committed metadata and the candidate are labeled
separately. `recovery_failed` means service restoration was not confirmed, even
when the recorded committed version is still V2.

## Build and run

Run from the repository root with the existing `.venv` and Qt 5.15 C++ toolchain.
Build the dashboard first using [QT_CLUSTER.md](QT_CLUSTER.md#build-on-pc).

```bash
# System Qt:
.venv/bin/python dashboard/qt_progress/build.py

# Or this host's existing extracted Qt toolchain:
.venv/bin/python dashboard/qt_progress/build.py \
  --qt-prefix /home/jsy/Downloads/volvo-qt-runtime/root/usr

# Observe an existing updater directory (read-only):
dashboard/qt_progress/run --root /PATH/DEVICE_ROOT --x 1280 --y 0

# Desktop presentation; choose a fresh output directory each time:
.venv/bin/python -m demo.qt_present --scenario mixed --output .demo/qt-live-mixed
.venv/bin/python -m demo.qt_present --scenario attack --output .demo/qt-live-attack
```

Build output and the recorded Qt prefix live in ignored `.demo/progress-build`.
The launcher loads that runtime without changing the shell environment. A desktop
DISPLAY/WAYLAND_DISPLAY is required for presentation; a private Xvfb display can
also be used. `CAPSTONE_QT_LIVE=1` opts verifier children into that display. Default
tests continue to use hidden rendering. The dashboard opens at x=0 and the monitor
at x=1280; use a display at least 1710 pixels wide for both windows.

The presentation executes the real signed PC OTA scenario. On commit or successful
rollback it reopens the accepted app and feeds 65 km/h for viewing; that final
bench display is not evidence of target systemd provisioning. Refused recovery
never reopens the old app. Closing the panel or pressing Ctrl-C ends the
presentation and cleans up its children. The panel itself does not own updater
processes. Existing output directories are refused rather than overwritten.

## Verification evidence

```bash
.venv/bin/python -m pytest -q tests/test_progress.py tests/test_qt_progress.py
.venv/bin/python -m pytest -q
```

Native panel tests launch the compiled C++/QML executable and read properties from
the loaded QML window; they do not replace it with a Python UI model. They cover
measured percentage, missing/invalid data, stale/future timestamps, dead writer,
successful rollback and refused recovery. They skip explicitly when the compiled
panel is absent. Actual Qt scenario tests verify retained 62/45 evidence after V2
recovery, alongside the existing signed two-hop compatible/mixed/attack checks.

Telemetry failure regressions cover an unwritable status file, a terminal display
notification that must not add a verdict-affecting event write, and SIGALRM during
status output. The download deadline uses a distinct TimeoutError subclass so
optional telemetry's OSError handling cannot swallow the timeout.

The existing 2026-10-27 live captures were inspected again on 2026-11-03:
`~/Downloads/CAPSTONE_OTA_Qt_진행패널_2026-10-27/V2_복구완료.png` shows the actual
V2 dashboard beside the completed rollback panel and retained 62/45 mismatch;
`V1_실행차단.png` records refused recovery. Companion JSON files retain the
corresponding updater snapshots. These local artifacts and demo signing keys are
not committed. Fresh full-suite results are recorded in the implementation plan.

Physical Pi display placement, remote telemetry transport, ARM compilation and
actual power interruption remain unverified by this panel work. This is read-only
process/file telemetry, not an independent camera or a trusted security display.
