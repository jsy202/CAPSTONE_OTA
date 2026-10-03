# Zonal OTA Application Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Central Control app (0x100/0x200/maintenance IPC) and the Qt Cluster `vehicle`/`functional` IPC so the zonal OTA can commit, roll back on a real semantic defect, and recover with real applications.

**Architecture:**
- **Central:** a Python slot payload. Its identity comes from the reused sanitized status file; it runs a deterministic scheduler and a strict maintenance IPC server.
- **Cluster:** Qt overlay classes on the GUI event loop that write to and read back from the upstream models. The fault is a compile-time speed-interpretation defect.
- **Verification:** extends the existing verifier with gates A1–A9.

**Tech Stack:** Python 3.10+, pytest, Qt 5.15 (QtNetwork `QLocalServer`, QtTest), systemd.

**Spec:** `docs/superpowers/specs/2026-10-03-zonal-application-contracts-design.md`

## Global Constraints

- Never modify `capstone_ota/` (coordinator, common, agent incl. `ui_status.py`, publisher) or existing tests/harness. G6/A9 protected paths enforce this.
- IPC v1 envelope exactly as `common/application_ipc.py`. Max message 4096 bytes. One request per connection. 2 s per-connection deadline.
- Central defaults: heartbeat 1.0 s, vehicle 0.1 s. Counters are uint8, +1 per frame.
- Central maintenance on → vehicle `(0, 800, PARK, 0)`; heartbeats never stop.
- Central identity: `/run/capstone-ota-ui/central-control.json` via `read_previous`. Unknown, missing or non-numeric → no heartbeat.
- Sockets `0660`; Central group `central-control`, Cluster group `digital-cluster`. Remove an existing path only if `lstat` reports a socket.
- Cluster lamp bit order (bit 0→7): `checkEngineLightModel`, `absWarningLightModel`, `batteryWarningLightModel`, `oilWarningLightModel`, `srsWarningLightModel`, `parkingBrakeLightModel`, `brakeFailureLightModel`, `bulbFailureLightModel` (property `on`).
- Fault: only with `qmake CAPSTONE_FAULT_SPEED_DIVISOR=n` (n ≥ 2). Startup log `DEMO/TEST FAULT INJECTION BUILD`.
- The upstream patch still touches exactly 4 files and only adds lines.

## Review Focus

1. Functional echo: a result equal to the request only because it was copied. Proven by the fault build returning 65 where the request was 650, and by a Qt test where the model rewrites the value.
2. A slow, partial or oversized client must not block the Qt GUI loop or the Central loop. Tests: a second request is served while the first client stays silent.
3. Identity drift: a heartbeat version or state that differs from the active slot. Test: status trial 1.1.0 → TRIAL (1,1,0); status unknown → no heartbeat.
4. Counter rollover 255→0 and no burst after a stall. Fake-clock tests.
5. A special file or symlink at the socket path: startup fails, never unlinks it.

---

### Task 1: Central core — identity, profile, scheduler
**Files:** Create `apps/central-control/lib/central_control.py`, `tests/unit/test_central_control.py`

**Produces:**
- `profile(t: float) -> tuple[int, int, Gear, int]`
- `MAINTENANCE_PROFILE`
- `read_identity(path) -> tuple[ApplicationState, tuple[int, int, int]] | None`
- `CentralControl(transport, identity_path, *, heartbeat_period=1.0, vehicle_period=0.1, protocol=(1, 0), clock=time.monotonic)` with `.tick(now) -> float` (next deadline), `.maintenance` (bool), `.set_maintenance(bool)`

- [x] Write the failing tests:
  - profile at the phase boundaries (0, 3, 10, 15, 20 s wraps to 0), all values inside `CanContract`, identical output for identical t
  - identity: stable/trial/unknown/missing/non-numeric/uint8 overflow
  - tick with a fake clock: exact counts over 10 s (10 heartbeats, 100 vehicle frames), contiguous counters through 255→0, no burst after a 5 s stall
  - no heartbeat while the identity is unknown, but vehicle frames continue
  - maintenance switches the vehicle frames to the stationary profile and keeps heartbeats
- [x] Run → FAIL (module missing)
- [x] Implement
- [x] Run → PASS
- [x] Commit `feat: add Central Control heartbeat, vehicle profile and identity`

### Task 2: Central maintenance IPC
**Files:** Modify `central_control.py`, test files

**Produces:**
- `handle_request(raw: bytes, app: CentralControl) -> bytes | None`
- `MaintenanceServer(path, app, *, group=None)` with `.fileno()`, `.service(deadline_clock)` (non-blocking accept/read, per-connection deadline)

- [x] Failing unit tests for `handle_request`:
  - valid on/off
  - echoed id
  - unknown operation → `ok:false`
  - wrong payload type, bool schema, duplicate keys, invalid UTF-8, >4096 bytes, no newline, non-object → `None`
- [x] Failing integration tests with the real `ApplicationIpc(...).set_maintenance` against a server in a thread:
  - on/off confirmed
  - socket mode 0660
  - a regular file or symlink at the path → startup refused, file untouched
  - a silent client does not block a second client (served within 1 s)
- [x] Implement with `selectors` and non-blocking sockets
- [x] PASS
- [x] Commit `feat: add Central Control maintenance IPC`

### Task 3: Central runtime, packaging, systemd
**Files:**
- Create: `apps/central-control/bin/central-control`, `apps/central-control/make-payload.sh`, `ota/systemd/capstone-ota-central-status.service`, `tests/integration/test_central_control_app.py`
- Modify: `ota/systemd/central-control.service`

**Produces:** `main(argv)` with flags `--can can0 --identity … --socket … --heartbeat-period --vehicle-period`. `run(app, server, transport_send)` loops until a stop event.

- [x] Failing tests:
  - wall-clock cadence: real `run()` for 3 s on a fake transport at 0.1/0.05 s; median ±20 %, max gap ≤ 2×
  - frames decode with the real codecs
  - payload script builds `bin/central-control` + `lib/central_control.py`, executable, and refuses a non-empty output
  - unit asset tests: `ExecStartPre=-+/usr/bin/setpriv …central-control.json`; `ExecStart` unchanged; `User=central-control`; `RestrictAddressFamilies=AF_UNIX AF_CAN`; no `ReadWritePaths`; watch unit hardened as `User=capstone-ota`
- [x] Implement
- [x] PASS
- [x] Commit `feat: package and deploy the Central Control application`

### Task 4: Cluster interpreter and IPC protocol (Qt, pure)
**Files:** Create `customization/overlay/app/inc/capstone/cluster_signals.h`, `src/capstone/cluster_signals.cpp`, `customization/qt-tests/cluster_signals_test.{pro,cpp}`

**Produces:**
- `struct WireSignals { int speed, rpm, gear, warnings; }`
- `class ClusterSignalInterpreter { static int faultDivisor(); void apply(const WireSignals&, ModelAccess&); WireSignals observe(ModelAccess&) const; }`
- `class ModelAccess` (interface over QObject property access, resolved by context-property name)
- `QByteArray handleIpcRequest(const QByteArray&, ClusterSignalInterpreter&, ModelAccess&)` (empty = close without response)

- [x] Failing Qt tests with test-local QObject models exposing the upstream property names:
  - 650 → speedo 65.0 kph / 40.39 mph and back to 650
  - rpm round-trip
  - warnings mask ↔ 8 lamps
  - gear kept
  - functional response built from the models after a model-side rewrite (the model clamps speed → the response shows the clamped value, proving no echo)
  - malformed envelopes (same partitions as Central), unknown op, missing model → `ok:false`
- [x] Implement
- [x] PASS
- [x] Commit `feat: interpret Cluster IPC signals through the dashboard models`

### Task 5: Cluster IPC server, patch, fault build
**Files:**
- Create: `customization/overlay/app/{inc,src}/capstone/cluster_ipc_server.*`, `customization/make-fault-payload.sh`
- Modify: the patch, `qt-tests` (fault build target), `tests/integration/test_dashboard_customization.py` (new expectations)

- [x] Failing tests:
  - Qt: QLocalServer test — a silent client plus a second client served; oversized input closed; stale socket replaced only when it is a socket
  - fault-build Qt test: `faultDivisor()==10` and 650 → model 6.5 kph → observe 65
  - static: the patch adds `cluster_ipc_server`, `QT += network`, the fault define only under `!isEmpty(CAPSTONE_FAULT_SPEED_DIVISOR)`; still 4 files, add-only
- [x] Implement, regenerate the patch from a pristine import, update `run-qt-tests.sh` to build normal + fault test binaries
- [x] Build the patched upstream normal and fault variants with `-Werror`
- [x] PASS
- [x] Commit `feat: serve Cluster vehicle and functional IPC from the dashboard`

### Task 6: Real Qt app IPC component test (conditional on `CAPSTONE_CLUSTER_APP` / `CAPSTONE_CLUSTER_FAULT_APP`)
**Files:** `tests/integration/test_cluster_app_ipc.py`

- [x] Tests (start the binary offscreen with a temp socket):
  - `ApplicationIpc.observe_functional_test(650,3000,D,1)` → exact
  - fault binary → speed 65
  - a vehicle sample, then functional → the response reflects the functional values
  - a malformed client and a silent client do not stop service
  - the process stays alive
- [x] Commit `test: verify the real Cluster app IPC contract`

### Task 7: Hardware-free system E2E with the real apps
**Files:** `tests/integration/test_application_contracts_system.py`

Use the existing `Vehicle` with monkeypatched `zonal_harness.CentralApp` (real `CentralControl` loop on the virtual port, identity from `run_once` on the central root at boot and restart) and `zonal_harness.ClusterApp` (real `ApplicationIpc` to the Qt process; the restart hook relaunches `active-slot/bin/application`).

- [x] Tests:
  - real Central + harness Cluster (unconditional) → COMMITTED; central heartbeats TRIAL 1.1.0 in the evidence; maintenance through real IPC `[on, off]`
  - real Central + real Qt normal → COMMITTED
  - real Central + real Qt fault build in slot B → `FUNCTIONAL_VALUE_MISMATCH`, ROLLED_BACK, recovery passed, both A
  - Central identity unknown during trial → `HEARTBEAT_*` → ROLLED_BACK
- [x] Commit `test: run the zonal OTA end to end with the real applications`

### Task 8: Verification measures, gates A1–A9, docs
**Files:** `verification_measures.json`, `scripts/verify_cluster_ota.py`, its unit tests, the REQUIREMENTS regeneration, `VERIFICATION_PLAN/REPORT`, `ZONAL_OTA_GUIDE.md` §5, `CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md` Part 2, README

- [x] Failing unit test: gates A1–A9 exist; A9 behaves like G6
- [x] Add VR-APP-001..014 with tests
- [x] Run the verifier with Qt/upstream/apps
- [x] Update the docs from the real results
- [x] Commit
