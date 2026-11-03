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
at x=1280; use a display at least 1900 pixels wide for both windows.

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

Physical Pi display placement and actual power interruption remain unverified.
Remote telemetry and ARM rendering are covered by the current section below. This is read-only
process/file telemetry, not an independent camera or a trusted security display.

## 2026-11-04: large comparison, A/B slots and real-board mirror

The current panel is620×960 logical pixels. Its large values are a verifier's
expected speed and observed Qt Gauge value. The first failed sample remains
explicitly labeled historical after recovery; a separate green line shows the
latest successful recovery sample. Equal values with `valid=false` are labeled
expired input rather than a zero speed difference.

A/B cards show versions from the trusted journal and transaction history. The
selection marker is a recorded selector, not proof that a process is healthy.
Green slot approval requires a selected slot, an external passed verdict and no
blocked/unconfirmed result. During recovery the route is amber; completed V2
recheck shows B→A in green. Recovery refusal leaves the normal record V2 visible
with a red failure state. The controlled attack fixture's V1 is labeled **시험
주입**, rather than treating unverified slot content as trusted metadata.

Comparison identity is captured with the first observed row: slot, version and
candidate/recovery phase. If candidate verification times out without samples,
a later V2 recovery mismatch is never attributed to V3.

Run a read-only monitor of the actual Cluster using the existing external bench
SSH configuration. Use a new output directory for each launch:

```sh
.venv/bin/python -m dashboard.qt_progress.remote \
  --hardware-config .demo/three-board-config.json \
  --output .demo/monitor-NEW_RUN --x 1280 --y 0
```

The mirror reads root-owned progress through the existing authenticated SSH
account. It issues no update, recovery or CAN commands. `progress.json` is a local
cache; `observations.jsonl` records observed changes. Closing the monitor stops
its viewer/polling and does not own the remote updater. Rapid intermediate stages
can fall between polls; download percentages are never invented to compensate.

The remote probe checks updater PID and writer age **on the Cluster**. The native
viewer checks the local receipt age separately. Missing/failed/stale transport
(over5seconds) hides healthy indicators even for a previously completed record.
Foreign PIDs and timestamps are not interpreted as local process identities or
cross-board latency measurements. Numeric samples appear when the verifier
returns; this is verdict telemetry, not a continuous CAN speed meter.

For a headless evidence observer, add `--no-window --seconds N`. The output root
must not already exist. A side-by-side desktop needs at least1900 logical pixels
in width and about1000 in height, including window decoration.

### ARM rendering and fonts

The panel was built with Pi2's native aarch64 Qt5toolchain and installed outside
A/B at `/opt/capstone-cluster/monitor/capstone-ota-progress`. Three actual-board
verdict records were rendered on PC and ARM under Xvfb; native snapshots agreed.
The initial ARM images exposed missing Korean glyphs despite matching strings.
Installed NotoSansCJK-Regular.ttc and its license, refreshed fontconfig, and
visually checked fresh Korean captures. `fc-match ':lang=ko'` now selects that
font on Pi2. The font is a system asset and is not committed in the repository.

Actual HDMI placement/optical display verification and monitor autostart are
separate work. Current evidence is native PC/ARM virtual-screen rendering and a
running laptop monitor of actual Pi2 records.
