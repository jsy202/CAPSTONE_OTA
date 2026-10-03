# Zonal OTA Application Contracts Design

Stacked on `feature/cluster-status-badge` (PR #1).

## 1. Purpose

Two-Pi hardware acceptance of the zonal OTA is blocked because the two
application contracts in `docs/ZONAL_OTA_GUIDE.md` §5 have no implementation:

| App | CAN output | IPC socket | IPC operations |
|---|---|---|---|
| Central Control | `0x100` heartbeat every 1 s, `0x200` vehicle status every 100 ms | `/run/central-control/ota.sock` | `maintenance` |
| Digital Cluster (Qt) | none (the zone agent sends the Cluster heartbeat) | `/run/digital-cluster/ota.sock` | `vehicle`, `functional` |

This design implements both contracts in **application code**. The OTA
framework (coordinator, zone agent, CAN codecs, `ApplicationIpc`, validator,
slots) is consumed unchanged.

## 2. Facts the design depends on

| Fact | Source |
|---|---|
| `ApplicationIpc`: one connection = one newline-terminated JSON request and one response. Request `{schema_version:1, request_id, operation, payload}`; response must have exactly `{schema_version, request_id, ok, result}`, an echoed `request_id`, and at most 4096 bytes. | `common/application_ipc.py` |
| `maintenance` result must equal `{"enabled": <bool>}`. `functional` result must be exactly four ints `{speed,rpm,gear,warnings}`. The `vehicle` result is ignored. | same |
| The coordinator turns maintenance **on before activation** and **off after commit or rollback**. Verification (heartbeats, 0x200, functional) runs while maintenance is on. | `coordinator/core.py` `_activate_verify_commit` / `_rollback` |
| The validator requires: central and cluster heartbeats with the bundle's exact version triple, protocol and state (TRIAL during trial, STABLE during recovery); contiguous counters; 0x200 inside the period budget and ranges (speed ≤ 3000, rpm ≤ 12000); and every functional result **exactly equal** to its request. | `coordinator/compatibility.py` |
| Probe challenges are fixed: `(650, 3000, DRIVE, 1)` and `(0, 800, PARK, 0)`, with speed in 0.1 km/h. | `coordinator/can_adapters.py:272` |
| The zone agent forwards each contiguous 0x200 to `apply_vehicle_status`. For each 0x610 it calls `observe_functional_test` and publishes the returned values on 0x611. | `agent/zonal.py` |
| Upstream QML reads context properties: `speedoModel.currentValue` (in `speedoModel.units`, `mph`/`kph`), `rpmModel.rpm`, and the lamp models `checkEngineLightModel`, `absWarningLightModel`, `batteryWarningLightModel`, `oilWarningLightModel`, `srsWarningLightModel`, `parkingBrakeLightModel`, `brakeFailureLightModel`, `bulbFailureLightModel` (`on`). Upstream has no gear model. | upstream `dash_host.h`, `WarningLightBar.qml`, `units.h` |
| `capstone-ota-ui-status` already turns a slot journal (`0600`) into a 4-field sanitized status file and is install-root generic. | PR #1 `agent/ui_status.py` |

## 3. Central Control application (`apps/central-control/`)

- **Language:** Python. It depends only on the framework's public codecs (`common.can_protocol`), `common.socketcan` and the safe reader `agent.ui_status.read_previous`.
- **Packaging:** it ships as a slot payload with `bin/central-control` (launcher) and `lib/central_control.py`. The launcher runs `/opt/capstone-ota/venv/bin/python`.

### 3.1 Runtime identity, no hard-coding
- `central-control.service` gains the same `ExecStartPre=-+/usr/bin/setpriv … capstone-ota-ui-status --install-root /opt/central-control --initial-version 1.0.0 --output /run/capstone-ota-ui/central-control.json`.
- A new `capstone-ota-central-status.service` (watch, `User=capstone-ota`) covers transitions without a restart.
- The app reads `/run/capstone-ota-ui/central-control.json` with `read_previous` before every heartbeat:
  - `stable` → `ApplicationState.STABLE`, `trial` → `TRIAL`.
  - The version must be a numeric triple fitting `uint8`.
  - On `unknown`, a missing file or a non-numeric version, **no heartbeat is sent**. Failing closed makes the validator time out, which causes a rollback; a guessed identity never appears on CAN.
- The privileged journal is never exposed. The helper is reused unchanged, only with a different install root.

### 3.2 Scheduler (deterministic, injectable clock)
- `CentralControl.tick(now)` sends every frame that is due and returns the next deadline. `run()` drives `tick` and the IPC server from one `selectors` loop with the real monotonic clock.
- Heartbeat period 1.0 s and vehicle period 0.1 s are configurable (CLI flags, default to the contract).
- Each stream has its own uint8 counter that increments by 1 per sent frame, modulo 256.
- Missed deadlines do not burst: if a deadline is in the past by more than one period, the schedule resynchronises to `now`.

### 3.3 Vehicle signal profile
`profile(t)` is a pure function with a 20 s cycle:

| Phase | Time | Speed | RPM | Gear |
|---|---|---|---|---|
| idle | 0–3 s | 0 km/h | 800 | PARK |
| accelerate | 3–10 s | 0 → 80 km/h | 1500 → 3000 | DRIVE |
| cruise | 10–15 s | 80 km/h | 2500 | DRIVE |
| decelerate | 15–20 s | 80 → 0 km/h | 2500 → 900 | DRIVE |

- Warnings are 0.
- All values are integers in wire units and always inside `CanContract`.

### 3.4 Maintenance semantics
- While maintenance is enabled, vehicle output switches to the **stationary safe profile** `(0, 800, PARK, 0)`. 0x200 keeps flowing at the same period.
- Heartbeats never stop. The validator needs both streams during verification, and stopping them would turn every update into a rollback.
- The maintenance flag lives in memory only. A restarted app starts with maintenance off; the coordinator re-sends maintenance on every phase.

### 3.5 Maintenance IPC server
- **Socket:**
  - A Unix stream socket at `/run/central-control/ota.sock`, created non-blocking with mode `0660`, group `central-control`.
  - `RuntimeDirectory` is `0750`. The coordinator runs as `capstone-ota` with `SupplementaryGroups=central-control`.
  - An existing path is unlinked only if it is a socket; any other file type makes startup fail.
- **Request limits:**
  - Read up to 4096+1 bytes or until a newline, with a 2 s per-connection deadline. One request per connection.
  - Strict UTF-8, JSON objects only, duplicate keys rejected.
  - Exact key set; `schema_version` is int 1 (bool rejected); `request_id` is 1–64 hex characters.
- **Operation:** `maintenance` only, with payload exactly `{enabled: bool}`.
- **Responses:**
  - Unknown operation or invalid payload → `ok:false, result:{}` with the echoed `request_id`.
  - Unparseable input or no `request_id` → the connection closes without a response; the client then reports `APPLICATION_IPC_INVALID`/`FAILED`.

## 4. Digital Cluster IPC (Qt overlay, `customization/`)

### 4.1 `ClusterSignalInterpreter` (pure C++, no Qt GUI)
- **Wire → display:**
  - `speed_kmh = speed / 10.0 / FAULT_DIVISOR`; mph when `speedoModel.units == "mph"` (÷ 1.609344).
  - `rpm = rpm`.
  - Gear is kept as an interpreted value; upstream has no gear display.
  - Warning bits 0–7 → the eight lamp models listed in §2, in that order.
- **Display → wire:** `speed = round(display_kmh × 10)` (from mph × 1.609344), the rpm, the interpreted gear, and the warning mask rebuilt from each lamp's `on`.
- `FAULT_DIVISOR` is 1 unless the binary is compiled with `CAPSTONE_FAULT_SPEED_DIVISOR=<n ≥ 2>` (static_assert).
- The fault sits **in the wire→display interpretation used by both `vehicle` and `functional`**, so the displayed speedometer is equally wrong. The functional result is never manipulated separately.

### 4.2 `ClusterIpcServer` (QLocalServer on the GUI event loop)
- **Event-driven:** no blocking accept or read. Each connection has a 2 s timer, a 4096-byte cap, and one request.
- **`vehicle`:** interpret, then write to the real models via `QObject::setProperty` on the context-property objects. These are the same objects QML binds to.
- **`functional`:** apply the same way, then **read back from the models** (`property()`), convert display → wire, and respond.
  - Nothing is read from the request after it is applied.
  - The write and the read-back happen in one event-loop turn on the GUI thread, so no 0x200 sample can interleave.
- **Model lookup:** models are resolved from the root `QQmlContext` at request time. A missing model → `ok:false`.
- **Socket path:** `CAPSTONE_CLUSTER_IPC_SOCKET` (default `/run/digital-cluster/ota.sock`), `UserAccessOption|GroupAccessOption`. A stale socket is removed only if `lstat` says socket.
- **Validation:** same as Central, except duplicate keys. Qt's parser keeps the last value; the socket is reachable only by `digital-dash` and the `digital-cluster` group (zone agent).
- **Patch:** the same four upstream files. `main.cpp` constructs the server next to `AppStatus`; `app.pro` adds the sources, `QT += network` and the opt-in fault define.

### 4.3 Fault build (DEMO / TEST FAULT INJECTION ONLY)
- **Build:** `qmake CAPSTONE_FAULT_SPEED_DIVISOR=10 …` produces a separate binary.
- **Identification:**
  - The binary logs `DEMO/TEST FAULT INJECTION BUILD: speed divisor 10` at startup.
  - `customization/make-fault-payload.sh` labels the payload `FAULT-INJECTION-ONLY`.
- **Default build:** the define is absent, so `FAULT_DIVISOR == 1`. A static test checks that the patch adds the define only under `!isEmpty(CAPSTONE_FAULT_SPEED_DIVISOR)`.

## 5. Deployment (systemd)

| Unit | Change |
|---|---|
| `central-control.service` | `ExecStartPre=-+setpriv … capstone-ota-ui-status` for central. `ExecStart` unchanged (`active-slot/bin/central-control`). Keeps `User=central-control`, `RestrictAddressFamilies=AF_UNIX AF_CAN`, `RuntimeDirectory=central-control` (0750) and all hardening. |
| `capstone-ota-central-status.service` | New watch unit, same hardening as the Cluster watcher. |
| `digital-cluster.service` | Unchanged from PR #1. No `RestrictAddressFamilies` is added: upstream may use SocketCAN sensors, GPS serial or eglfs/udev (netlink) on the Pi, and the IPC socket lives in its existing `RuntimeDirectory`. |

No application runs as root, and no OTA unit gains privileges.

## 6. Verification design

New requirements VR-APP-001..014 go into `verification_measures.json`. They
map to the new gates A1–A9, which are evaluated with G1–G7 by the existing
verifier. A failing gate yields FAIL. A skipped conditional test yields
NOT_EXECUTED, and the overall result is then INCOMPLETE.

| Gate | Scope | Main tests |
|---|---|---|
| A1 | Central unit: heartbeat, counters, identity, profile, maintenance, scheduler (fake clock) | `tests/unit/test_central_control.py` |
| A2 | Cluster IPC/model integration: Qt unit tests of interpreter + server; real patched app answers IPC from its models | Qt Test, `test_cluster_app_ipc.py` (needs Qt) |
| A3 | Protocol conformance: Central IPC ↔ real `ApplicationIpc` client; Central frames decode with the real codecs; wall-clock cadence within tolerance | `tests/integration/test_central_control_app.py` |
| A4 | Normal compatibility: real Central app + real zone agent + real coordinator (+ real Qt app when available) → COMMITTED | `tests/integration/test_application_contracts_system.py` |
| A5 | Semantic fault: fault build shows 6.5 km/h on the real model and causes `FUNCTIONAL_VALUE_MISMATCH` | Qt Test (fault build), system test |
| A6 | Whole-vehicle rollback with the real apps | system test |
| A7 | Recovery verification: STABLE heartbeats from the real Central identity after rollback | system test |
| A8 | Security/privilege: socket modes, special-file refusal, unit hardening, no root | unit + asset tests |
| A9 | Full regression + change impact (same as G6) | verifier |

**Timing.**
- Unit tests use a fake clock and run exact tick counts.
- The wall-clock integration test runs the real Central app for 3 s, at a 0.1 s heartbeat and 0.05 s vehicle period (the harness contract), and accepts:
  - median inter-frame interval within ±20 % of nominal;
  - no gap above 2× nominal.

  These are well inside the validator's budget (`max_missed_heartbeats`, `heartbeat_timeout_s`) and tolerant of CI scheduling jitter.

**Fault injection.** New tests are added for:
- Central side: heartbeat stopped, stale/duplicate counter, wrong version, wrong state, malformed IPC.
- Cluster side: socket missing, IPC timeout, malformed result, process killed, stale/echo result, fault build.

Existing OTA fault tests are linked in the traceability, not rewritten.

## 7. Change impact

| | Areas |
|---|---|
| Unchanged | coordinator, slots, zonal agent, CAN codecs, vehicle bundle, signature/artifact verification, validator, rollback semantics, `ui_status.py`, zonal harness and existing tests. Enforced by G6/A9 protected paths. |
| Changed | new `apps/central-control/`; new Qt overlay sources; the patch (same 4 files, add-only); `central-control.service`; a new watch unit; tests and docs. `digital-cluster.service` stays unchanged. |

## 8. Non-goals and limitations

- No gear display is drawn; gear is interpreted and reported, not shown.
- On the Pi, upstream `Dash` sensor sources (ADC, pulse counter, MCP23017 lamps) may also write the same models when that hardware is present. This is a hardware acceptance item.
- The desktop `DashHost` animates RPM and temperatures with timers, so the display can briefly differ from the IPC values. Functional read-back is unaffected because write and read happen in one event-loop turn.
- No Central Control UI; no CAN authentication (unchanged threat model).
