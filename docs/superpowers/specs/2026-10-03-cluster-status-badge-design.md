# Digital Cluster OTA Status Badge Design

## 1. Purpose

During the zonal OTA demonstration, a viewer must be able to tell from the
Cluster screen alone which application build is running and whether it is the
stable or the trial slot:

```text
STABLE v1.0.0  ->  OTA TRIAL v1.1.1  ->  STABLE v1.0.0 (after rollback)
```

The change adds one small status badge to the existing Volvo cluster UI. The
original gauges, warning lights, blinkers, odometer and layouts stay as they
are.

Goals, in priority order:

1. **Primary:** show `STABLE` or `OTA TRIAL` plus the software version of the
   application that is actually running.
2. **Secondary (optional):** for a short time right after a whole-vehicle
   rollback, show `ROLLBACK COMPLETE` / `RESTORED <version>`, then fall back
   to the normal `STABLE <version>` badge.

## 2. Constraints

- No change to OTA core logic: coordinator, `ABSlotInstaller`, zone agent,
  CAN protocol, application IPC, rollback, or the existing tests.
- The UI must not hard-code state or version strings. Both come from the OTA
  runtime state at run time.
- No OTA protocol change just to carry a rollback event.
- Upstream Volvo240-DigitalDash stays pinned and provenance-preserving.
  Changes are a reproducible overlay plus a patch, applied after
  `import-upstream.sh`.
- Must be simple and robust on a Raspberry Pi 4B with Qt 5.15.
- A missing, malformed or unknown status must never crash the application.

## 3. Facts From the Existing Code

| Fact | Source | Consequence |
|---|---|---|
| `digital-cluster.service` runs `/opt/digital-cluster/active-slot/bin/digital-dash` as user `digital-dash` | `ota/systemd/digital-cluster.service` | The running slot is the displayed build |
| Activation saves `phase=activating`, selects the trial slot, then restarts the app | `capstone_ota/agent/slots.py` `activate_trial` | At app launch, the slot state already identifies trial |
| Rollback saves `phase=rolling_back`, selects the stable slot, then restarts the app | `slots.py` `rollback` | At app launch, the state identifies a restore |
| Commit rewrites state only; the app is **not** restarted | `slots.py` `commit`/`_finish` | Status must update while the app runs |
| `state.json` is created by `mkstemp`, so it is `0600` and owned by `capstone-ota` | `SlotState.save_atomic` | The app cannot read it; changing that would touch OTA code |
| `trial_version`/`stable_version` are recorded. The provisioned A slot has `stable_version = null`, and the zone agent supplies `--initial-version` | `SlotState`, `capstone-ota-zone-agent.service` | Version comes from OTA state, with the same fallback |
| Heartbeat uses `trial = trial_slot is not None and trial_slot == active_slot` and reports `ApplicationState.STABLE/TRIAL` | `capstone_ota/agent/zonal.py` `publish_heartbeat` | Reuse the same rule and enum names for the UI state |
| Upstream window is 1280x480. On a Pi the gauge item is rotated 180 degrees; warning lights sit in a top-center row and blinkers at the top | upstream `app/main.qml`, `WarningLightBar.qml` | The badge goes inside `gaugeItem`, bottom-right |
| `main.cpp` sets context properties before `engine.load("qrc:/main.qml")` | upstream `app/src/main.cpp` | Expose one `capstoneStatus` object there |

Reusing the application IPC or the CAN heartbeat for UI display was
considered and rejected:
- IPC is a request/response channel owned by the zone agent for validation.
  Adding a push operation would change the zone agent.
- Decoding the heartbeat inside the UI would couple it to the OTA CAN
  protocol.

## 4. Architecture

```text
/opt/digital-cluster/state.json (0600, read only)  + active-slot selector
        |
        |  capstone-ota-ui-status   (new, read-only helper, pure decision function)
        |    a) ExecStartPre=-+ snapshot in digital-cluster.service (exact state at app launch)
        |    b) --watch service polling every 1 s (commit and other non-restart changes)
        v
/run/capstone-ota-ui/digital-cluster.json (0644)
  {"schema_version":1,"state":"trial","version":"1.1.1","restored_at":null}
        |
        |  AppStatus (C++ QObject, polls every 1 s; env fallback)
        v
StatusBadge.qml (bottom-right inside gaugeItem) + thin bottom accent strip
```

### 4.1 Status helper (`capstone_ota/agent/ui_status.py`)

The helper is a new console script, `capstone-ota-ui-status`. It only reads
OTA state; it never writes the slot directories or `state.json`.

```text
capstone-ota-ui-status --install-root /opt/digital-cluster --initial-version 1.0.0
                       --output /run/capstone-ota-ui/digital-cluster.json [--watch --interval 1.0]
```

The pure function is
`decide(slot_state, selected_slot, initial_version, previous, now) -> dict`:

| Condition | `state` | `version` |
|---|---|---|
| `trial_slot` is not null and `selected_slot == trial_slot` | `trial` | `trial_version` |
| otherwise | `stable` | `stable_version`, or `initial_version` when null |
| state unreadable, invalid, or selector missing | `unknown` | `null` |

- `state` values are the lowercase `ApplicationState` member names (`stable`,
  `trial`) plus `unknown`.
- `SlotState.load` is reused for strict parsing.
- The selector is read with `os.readlink` on `active-slot`. Only a target of
  `slots/A` or `slots/B` is accepted.

**`restored_at` (secondary goal)** is a Unix time in seconds (float). It is set to `now` only when the previous
output had `state == "trial"`, the new state is `stable`, and the new version
differs from the previous trial version. This is the visible transition
`trial 1.1.1 -> stable 1.0.0`.
- It is carried over unchanged while the state stays `stable` with the same
  version, and cleared otherwise.
- A commit (`trial 1.1.1 -> stable 1.1.1`) and an abort before activation
  (previous state already `stable`) never set it.
- After a reboot `/run` is empty, so a power-loss recovery shows no banner.
  This limit is accepted.

**Output**
- The output is written atomically: `mkstemp` in the output directory, then
  `fchmod 0644`, `fsync`, and `os.replace`.
- It is rewritten only when the content changes.
- The helper never creates the output directory; tmpfiles owns it. A missing
  directory is reported on stderr.
- The helper always exits 0 in snapshot mode. In watch mode it never stops on
  a read error: it writes `unknown` and keeps polling.

### 4.2 systemd assets

- **`digital-cluster.service`** gains
  `ExecStartPre=-+/opt/capstone-ota/venv/bin/capstone-ota-ui-status ...`
  - `+` runs the helper privileged, so it can read the `0600` state.
  - `-` lets the app start even if the helper fails.
  - `ExecStart` and the hardening lines are unchanged, and the unit still has
    no `ReadWritePaths`.
- **`capstone-ota-ui-status.service`** (new) runs the helper with `--watch`
  as `User=capstone-ota`, which owns `state.json`. It is hardened with
  `ProtectSystem=strict`, `ReadWritePaths=/run/capstone-ota-ui`,
  `NoNewPrivileges=true` and `RestrictAddressFamilies=AF_UNIX`.
- **`ota/tmpfiles/capstone-ota-ui.conf`** (new) creates
  `d /run/capstone-ota-ui 0755 capstone-ota capstone-ota -`.
- `--initial-version` must match the zone agent's value. The guide states
  this next to the existing note.

### 4.3 Qt customization (overlay + patch)

```text
dashboard/volvo-digital-dash/customization/
  overlay/app/inc/capstone/app_status.h
  overlay/app/src/capstone/app_status.cpp
  overlay/app/StatusBadge.qml
  patches/0001-capstone-ota-status-badge.patch
  apply-customization.sh
  qt-tests/app_status_test.pro, app_status_test.cpp  (standalone Qt Test; builds the
                                                    overlay AppStatus, not part of upstream)
```

**`apply-customization.sh <imported-dir>`**
- Refuses to run unless the target is the pinned import and the customization
  is not yet applied. A marker file records an applied customization.
- Copies `overlay/` and runs `patch -p1 --forward`.
- Fails on any rejected hunk.

**The patch** touches only these places:
- `app/src/main.cpp`: include `app_status.h`, construct one `AppStatus`, and
  call `ctxt->setContextProperty("capstoneStatus", ...)` next to the existing
  `keyPressEmitter` property.
- `app/app.pro`: add the source, header and `INCLUDEPATH`.
- `app/qml.qrc`: add `StatusBadge.qml`.
- `app/main.qml`: add one `StatusBadge { status: capstoneStatus }` as the last
  child of `gaugeItem`, with `z` above the gauges.

**`AppStatus`** (Qt Core only)
- Properties: `state` (`stable`/`trial`/`restored`/`unknown`), `version`,
  `title`, `detail`, `bannerActive`.
- Source order:
  1. The JSON file at `CAPSTONE_APP_STATUS_FILE`, default
     `/run/capstone-ota-ui/digital-cluster.json`.
  2. When the file is missing, the env vars `CAPSTONE_APP_STATE` and
     `CAPSTONE_APP_VERSION`. These serve desktop demos and tests.
  3. Otherwise `unknown`.
- Decision function: static
  `AppStatus::Snapshot AppStatus::evaluate(QByteArray json, qint64 nowMs)`.
  - It rejects anything that is not `schema_version 1`, an unknown `state`,
    or a version not matching `[0-9A-Za-z][0-9A-Za-z.+-]{0,31}`; these become
    `unknown`.
  - `restored` is reported while `state == stable` and
    `0 <= now - restored_at < 15 s`.
- A `QTimer` re-reads every 1000 ms and emits change signals only on change.
  Files larger than 4 KiB are rejected.

**`StatusBadge.qml`** has a fixed visual contract:

| Display state | Title / detail | Accent |
|---|---|---|
| stable | `● STABLE` / `SW 1.0.0` | green dot `#3FB950`, light gray text, dark translucent plate; no strip |
| trial | `● OTA TRIAL` / `SW 1.1.1` | cyan `#22D3EE` dot, border and 4 px bottom strip |
| restored (secondary) | `↺ ROLLBACK COMPLETE` / `RESTORED SW 1.0.0` | soft amber `#F5B841` border and strip, about 15 s only |
| unknown | `SW STATUS` / `—` | gray `#8B949E` |

- Size: the title is 22 px bold and the detail 18 px, on a plate about 64 px
  tall with a 12 px margin from the bottom-right edge. It must be readable on
  camera.
- No pop-ups or full-screen overlays. Red is never used.
- The plate has `enabled: false`, so it never takes key events.
- Trial uses cyan and not amber because amber is the color of real Volvo
  warning lamps.

## 5. Data Flow in the Demo Scenario

| Step | Slot state | Helper output | Screen |
|---|---|---|---|
| 1. stable A | `stable`, active A, `stable_version` null | `stable`, `1.0.0` (initial) | `● STABLE  SW 1.0.0` |
| 2. prepare | `staging`/`staged`, active A | `stable`, `1.0.0` | unchanged |
| 3. activate B | `activating`, active B, app restarts | `trial`, `1.1.1` (ExecStartPre) | `● OTA TRIAL  SW 1.1.1` + cyan strip |
| 4. verification fails | `trial` | `trial` | unchanged |
| 5. rollback | `rolling_back`, active A, app restarts | `stable`, `1.0.0`, `restored_at=now` | `ROLLBACK COMPLETE / RESTORED SW 1.0.0` (15 s) |
| 6. after 15 s | `stable`, active A | same file | `● STABLE  SW 1.0.0` |
| Commit path | `committing` → `stable` B, no restart | `trial` → `stable 1.1.1` via watch (≤ 1 s + 1 s poll) | `● STABLE  SW 1.1.1` |

## 6. Error Handling

- The helper fails closed to `unknown`, never to a guessed version.
- The UI treats anything it cannot validate as `unknown` and keeps all other
  dashboard behavior unchanged.
- Helper failure never blocks the cluster app (`-` prefix) or the OTA
  services, which do not depend on it.

## 7. Testing

| Check | How | Evidence |
|---|---|---|
| Decision rules: stable, trial, initial-version fallback, unknown on corrupt or missing state or bad selector | pytest unit tests on `decide()` and the reader | A |
| Real slot lifecycle: a real `ABSlotInstaller` with a fake service manager whose restart hook runs the helper snapshot. Assert `1.0.0 stable → 1.1.1 trial → 1.0.0 stable` (and `restored_at` set), plus commit → `1.1.1 stable` without `restored_at`, and a staged abort without `restored_at` | pytest | A |
| Atomic write, `0644` mode, no rewrite when unchanged, watch loop survives errors | pytest | A |
| systemd and tmpfiles assets: unit strings, `-+` prefix, no `ReadWritePaths` on the app unit, watch hardening | pytest | A |
| Customization: overlay files exist, patch only touches the four allowed files, script refuses double application. Optional test applies the patch to a real pinned import when `CAPSTONE_UPSTREAM_DIR` is set | pytest (+ local opt-in run) | A |
| Full regression including zonal integration (651 at baseline) | `python3 -m pytest -q` | A |
| `AppStatus::evaluate` cases and QML badge rendering | `customization/qt-tests` standalone Qt Test project. Qt is not installed on the dev PC, so build and run happen on the Pi/build host | B until run |
| Gauges, RPM, gear and warning lights unaffected; badge readable on camera | Hardware checklist items | C until run |

## 8. Non-Goals

- Any change to OTA, CAN, IPC, coordinator or slot code paths.
- Central Control UI, and the legacy single-Pi `digital-dash.service`.
- Persisting the rollback banner across reboots.
- Changing upstream layouts, colors or gauges.
