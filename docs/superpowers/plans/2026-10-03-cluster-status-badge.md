# Cluster OTA Status Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show `STABLE`/`OTA TRIAL` plus the running SW version, and briefly `ROLLBACK COMPLETE` after a rollback, on the Digital Cluster screen. OTA code paths stay unchanged.

**Architecture:**
- A read-only Python helper turns the cluster slot state into a small status
  JSON under `/run/capstone-ota-ui/`.
- The helper runs in two modes: as a privileged `ExecStartPre` snapshot at app
  launch, and as a watch service for non-restart transitions.
- A Qt overlay (`AppStatus` + `StatusBadge.qml`), applied to the pinned
  upstream by patch, polls that file and draws a corner badge.

**Tech Stack:** Python 3.10+, pytest, systemd/tmpfiles, Qt 5.15 (Core/QML/Test), GNU patch.

**Spec:** `docs/superpowers/specs/2026-10-03-cluster-status-badge-design.md`

## Global Constraints

- Do not modify `capstone_ota/coordinator/*`, `capstone_ota/agent/slots.py`, `capstone_ota/agent/zonal*.py`, `capstone_ota/common/*`, or any existing test.
- Status file: `/run/capstone-ota-ui/digital-cluster.json`, mode `0644`, schema `{"schema_version":1,"state":"stable"|"trial"|"unknown","version":str|null,"restored_at":float|null}`.
- States reuse the lowercase `ApplicationState` names (`stable`, `trial`) plus `unknown`.
- Trial rule mirrors `zonal.py` `publish_heartbeat`: `trial_slot is not None and selected_slot == trial_slot`.
- Restored banner window: 15 s. UI poll interval: 1000 ms. Watch interval default: 1.0 s.
- Colors: stable dot `#3FB950`, trial `#22D3EE`, restored `#F5B841`, unknown `#8B949E`. Never red.
- Text: `● STABLE` / `SW <v>`; `● OTA TRIAL` / `SW <v>`; `↺ ROLLBACK COMPLETE` / `RESTORED SW <v>`; `SW STATUS` / `—`.
- UI version regex `[0-9A-Za-z][0-9A-Za-z.+-]{0,31}`. Max status file size 4096 bytes.
- The upstream patch touches only `app/src/main.cpp`, `app/app.pro`, `app/qml.qrc`, `app/main.qml`.
- Pinned upstream commit `793452919127065536bcb7a08f98838fa963d75e`. `import-upstream.sh` is unchanged.

## Review Focus

1. Corrupt or partially written `state.json`, or a dangling or foreign `active-slot`. The helper must emit `unknown`, never a stale or guessed version. Tests in Task 1.
2. A staged abort (rollback with `phase=staged`, no restart) must not show `ROLLBACK COMPLETE`. Test in Task 3.
3. A commit (`trial 1.1.1 → stable 1.1.1`) must not show `ROLLBACK COMPLETE`. Test in Task 3.
4. A missing `/run/capstone-ota-ui` or an unwritable output must not raise in snapshot mode and must not end the watch loop. Tests in Task 2.
5. A status file with a hostile version (long string, control characters, HTML) must render as `unknown` in the UI. Qt test in Task 5.

---

### Task 1: Status decision and slot reader

**Files:**
- Create: `capstone_ota/agent/ui_status.py`
- Test: `tests/unit/test_ui_status.py`

**Interfaces:**
- Produces:
  - `SlotSnapshot(state: SlotState | None, selected_slot: str | None)`, a frozen dataclass
  - `read_snapshot(install_root: Path) -> SlotSnapshot`
  - `decide(snapshot: SlotSnapshot, initial_version: str, previous: dict | None, now: float) -> dict`
  - `SCHEMA_VERSION = 1`

- [ ] **Step 1: Write failing tests**

```python
from dataclasses import replace

from capstone_ota.agent.slots import SlotState
from capstone_ota.agent.ui_status import SlotSnapshot, decide, read_snapshot

TX = "00000000-0000-4000-8000-000000000001"
TRIAL = SlotState(phase="trial", active_slot="B", trial_slot="B", transaction_id=TX,
                  trial_version="1.1.1", trial_entrypoint="bin/digital-dash", trial_digest="0" * 64)

def test_provisioned_stable_uses_initial_version():
    assert decide(SlotSnapshot(SlotState(), "A"), "1.0.0", None, 0.0) == {
        "schema_version": 1, "state": "stable", "version": "1.0.0", "restored_at": None}

def test_selected_trial_slot_reports_trial_version():
    assert decide(SlotSnapshot(TRIAL, "B"), "1.0.0", None, 0.0)["state"] == "trial"
    assert decide(SlotSnapshot(TRIAL, "B"), "1.0.0", None, 0.0)["version"] == "1.1.1"

def test_activating_before_selector_switch_is_still_stable():
    activating = replace(TRIAL, phase="activating", active_slot="A")
    assert decide(SlotSnapshot(activating, "A"), "1.0.0", None, 0.0)["state"] == "stable"

def test_unreadable_state_is_unknown():
    assert decide(SlotSnapshot(None, None), "1.0.0", None, 0.0) == {
        "schema_version": 1, "state": "unknown", "version": None, "restored_at": None}

# read_snapshot against tmp_path:
#   missing state.json                          -> SlotSnapshot(None, None)
#   state.json "{not json"                      -> SlotSnapshot(None, None)
#   active-slot -> "slots/C" or "/etc"          -> selected_slot None -> decide == unknown
#   valid installer root (ABSlotInstaller init) -> state.phase == "stable", selected_slot == "A"
```

- [ ] **Step 2: Run** `python3 -m pytest tests/unit/test_ui_status.py -q`. Expected: FAIL (`ModuleNotFoundError: capstone_ota.agent.ui_status`).

- [ ] **Step 3: Implement**
  - `read_snapshot` uses `SlotState.load(install_root / "state.json")` and `os.readlink(install_root / "active-slot")`. It accepts only the targets `slots/A` and `slots/B`, or the absolute `install_root/slots/A|B`; `_select` writes a relative or absolute link, so check both. Any `OtaError`/`OSError` returns `None` for that field.
  - `decide` returns `unknown` whenever either field is `None`.
  - `restored_at` stays `None` in this task; Task 3 adds the rule.
  - The module docstring states that it is read-only and mirrors `zonal.py` `publish_heartbeat`.

- [ ] **Step 4: Run** the Step 2 command. Expected: all PASS.

- [ ] **Step 5: Commit** `feat: derive cluster UI status from slot state`

### Task 2: Atomic status output, snapshot/watch CLI, real lifecycle

**Files:**
- Modify: `capstone_ota/agent/ui_status.py`
- Modify: `pyproject.toml` (add `capstone-ota-ui-status = "capstone_ota.agent.ui_status:main"`)
- Test: `tests/unit/test_ui_status.py`, `tests/integration/test_ui_status_lifecycle.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `read_previous(path: Path) -> dict | None`
  - `write_status(path: Path, status: dict) -> bool`
  - `run_once(install_root: Path, initial_version: str, output: Path, now: Callable[[], float] = time.time) -> dict`
  - `watch(install_root, initial_version, output, interval: float, *, iterations: int | None = None, sleep=time.sleep, now=time.time) -> None`
  - `main(argv: list[str] | None = None) -> int`, with CLI `--install-root --initial-version --output [--watch] [--interval]`

- [ ] **Step 1: Write failing unit tests**
  - `test_write_status_is_atomic_0644_and_skips_identical`: the first call returns `True` and the file mode is `0o644`; a second identical call returns `False` and the `st_mtime_ns` is unchanged.
  - `test_write_status_does_not_create_missing_directory`: `write_status(tmp_path/"missing"/"x.json", ...)` raises `OSError`, and the directory does not exist afterwards.
  - `test_main_snapshot_returns_zero_when_output_directory_missing`: `main([... "--output", str(tmp_path/"missing/x.json")]) == 0`.
  - `test_watch_survives_corrupt_state_and_recovers`: corrupt `state.json` → the output state is `unknown`; restore a valid state → `stable`, with `iterations=2` and a no-op sleep.
  - `test_read_previous_rejects_non_v1`: `read_previous` returns `None` for garbage, a file over 4096 bytes, or `schema_version != 1`.

- [ ] **Step 2: Write the failing lifecycle test** in `tests/integration/test_ui_status_lifecycle.py`.
  - Use a real `ABSlotInstaller(root, services, service_unit="digital-cluster.service")`.
  - Use a fake `services.restart_and_wait_healthy` that calls `run_once(root, "1.0.0", out)` and records `read_previous(out)`. This simulates `ExecStartPre` at app restart.
  - `test_trial_then_rollback_shows_1_0_0_1_1_1_1_0_0`:
    - `run_once` before → `("stable","1.0.0")`
    - `stage(TX, app, "bin/digital-dash", "1.1.1")`, then `activate_trial(TX, rollback_on_failure=False)` → the restart snapshot is `("trial","1.1.1")`
    - `rollback(TX)` → the restart snapshot is `("stable","1.0.0")`
  - `test_commit_reaches_stable_new_version_via_watch`: after the trial, `commit(TX)` and then `watch(..., iterations=1)` → `("stable","1.1.1")`.

- [ ] **Step 3: Run** `python3 -m pytest tests/unit/test_ui_status.py tests/integration/test_ui_status_lifecycle.py -q`. Expected: FAIL (missing functions).

- [ ] **Step 4: Implement**
  - `write_status`: `mkstemp(dir=path.parent, prefix=".digital-cluster.")`, `fchmod 0o644`, write `json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n"`, `fsync`, `os.replace`. Return `False` when the existing content is byte-identical.
  - `run_once`: `read_snapshot` → `decide(previous=read_previous(output))` → `write_status`.
  - `main`, snapshot mode: catch every exception, print to stderr, return 0.
  - `watch`: loop with per-iteration `try/except Exception` that writes `unknown` best-effort and continues.

- [ ] **Step 5: Run** the Step 3 command. Expected: PASS.

- [ ] **Step 6: Commit** `feat: publish cluster UI status snapshots and watch updates`

### Task 3: Secondary goal — `restored_at` after a visible rollback

**Files:**
- Modify: `capstone_ota/agent/ui_status.py` (`decide` only)
- Test: `tests/unit/test_ui_status.py`, `tests/integration/test_ui_status_lifecycle.py`

**Interfaces:**
- Consumes: `decide`, `run_once`, `watch` from Tasks 1–2. No signature changes.

- [ ] **Step 1: Write failing tests**
  - Unit `test_trial_to_stable_with_different_version_sets_restored_at`: `previous={"state":"trial","version":"1.1.1",...}` and a stable `1.0.0` snapshot at `now=100.0` → `restored_at == 100.0`.
  - Unit `test_restored_at_carries_over_while_stable_same_version`: previous stable `1.0.0` with `restored_at=100.0`, `now=200.0` → `100.0`.
  - Unit `test_commit_same_version_never_sets_restored_at`: previous trial `1.1.1`, now stable `1.1.1` → `None`.
  - Unit `test_trial_clears_restored_at`.
  - Lifecycle: extend the rollback test so the post-rollback snapshot has a float `restored_at`. The commit test asserts `restored_at is None`.
  - Lifecycle `test_staged_abort_never_shows_rollback`: `stage` then `rollback` (no restart). `watch(iterations=1)` → stable `1.0.0` with `restored_at is None`.

- [ ] **Step 2: Run** the Task 2 Step 3 command. Expected: the new tests FAIL.

- [ ] **Step 3: Implement** the spec §4.1 rule in `decide`:
  - Set `restored_at = now` when `previous.state == "trial"`, the new state is `stable`, and `version != previous.version`.
  - Carry over `previous.restored_at` when the previous and new state are both `stable` with the same version.
  - Otherwise `None`.

- [ ] **Step 4: Run** the Task 2 Step 3 command. Expected: PASS.

- [ ] **Step 5: Commit** `feat: mark visible rollback restores in cluster UI status`

### Task 4: systemd and tmpfiles assets

**Files:**
- Modify: `ota/systemd/digital-cluster.service`
- Create: `ota/systemd/capstone-ota-ui-status.service`, `ota/tmpfiles/capstone-ota-ui.conf`
- Test: `tests/integration/test_ui_status_assets.py` (new file; existing asset tests untouched)

**Interfaces:**
- Consumes: the CLI from Task 2.

- [ ] **Step 1: Write failing tests**
  - `digital-cluster.service` contains exactly this line: `ExecStartPre=-+/opt/capstone-ota/venv/bin/capstone-ota-ui-status --install-root /opt/digital-cluster --initial-version 1.0.0 --output /run/capstone-ota-ui/digital-cluster.json`.
  - It still contains `ExecStart=/opt/digital-cluster/active-slot/bin/digital-dash` and no `ReadWritePaths`.
  - `capstone-ota-ui-status.service` has:
    - `User=capstone-ota`, `ProtectSystem=strict`, `ReadWritePaths=/run/capstone-ota-ui`, `NoNewPrivileges=true`, `RestrictAddressFamilies=AF_UNIX`, `Restart=on-failure`, `WantedBy=multi-user.target`
    - an `ExecStart` with the same arguments plus `--watch --interval 1.0`
  - The `--initial-version` value in both units equals the one in `capstone-ota-zone-agent.service`, parsed by regex.
  - The tmpfiles line is exactly `d /run/capstone-ota-ui 0755 capstone-ota capstone-ota -`.

- [ ] **Step 2: Run** `python3 -m pytest tests/integration/test_ui_status_assets.py -q`. Expected: FAIL.

- [ ] **Step 3: Write the unit files and the tmpfiles line.** Add a comment above `ExecStartPre` explaining `-` (never block the app) and `+` (read the 0600 slot state).

- [ ] **Step 4: Run** the Step 2 command and `python3 -m pytest tests/integration/test_config_assets.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat: install cluster UI status snapshot and watch units`

### Task 5: Qt overlay — `AppStatus` model and `StatusBadge.qml`

**Files:**
- Create: `dashboard/volvo-digital-dash/customization/overlay/app/inc/capstone/app_status.h`
- Create: `dashboard/volvo-digital-dash/customization/overlay/app/src/capstone/app_status.cpp`
- Create: `dashboard/volvo-digital-dash/customization/overlay/app/StatusBadge.qml`
- Create: `dashboard/volvo-digital-dash/customization/qt-tests/app_status_test.pro`, `app_status_test.cpp`
- Test: `tests/integration/test_dashboard_customization.py` (static contract checks)

**Interfaces:**
- Consumes: the status file schema (Global Constraints).
- Produces, for Task 6:
  - `class AppStatus : public QObject` with
    `Q_PROPERTY(QString state READ state NOTIFY changed)`, and likewise `version`, `title`, `detail`, `bool bannerActive`
  - constructor `AppStatus(QObject *parent = nullptr)`, which reads `CAPSTONE_APP_STATUS_FILE`, defaulting to `/run/capstone-ota-ui/digital-cluster.json`, and starts a 1000 ms `QTimer`
  - `struct Snapshot { QString state; QString version; }`
  - `static Snapshot evaluate(const QByteArray &json, qint64 nowMs)`
  - `static Snapshot fromEnvironment()`
  - QML: `StatusBadge { property var status }`

- [ ] **Step 1: Write the Qt Test cases** in `qt-tests/app_status_test.cpp` (QTest; this cannot run on the dev PC):
  - `evaluate` with valid stable/trial JSON → state and version.
  - `restored_at = now-5 s` → `restored`; `now-16 s` → `stable`; a future `restored_at` → `stable`.
  - `schema_version 2`, `state "error"`, a 33-character version, a version containing `<b>` or `\n`, non-JSON input, or 5000 bytes → `unknown` with an empty version.
  - `fromEnvironment` with `CAPSTONE_APP_STATE=trial`, `CAPSTONE_APP_VERSION=1.1.1` → trial/1.1.1; an invalid env value → unknown.
  - `title`/`detail` strings for each state match Global Constraints exactly.

- [ ] **Step 2: Write failing static pytest checks** in `test_dashboard_customization.py`:
  - All overlay and qt-test files exist.
  - `StatusBadge.qml` contains the four color literals and no `red`/`#FF0000`/`#f00`, has `enabled: false`, and contains no version-number literal (regex `\d+\.\d+\.\d+`).
  - `app_status.cpp` contains `CAPSTONE_APP_STATUS_FILE`, `/run/capstone-ota-ui/digital-cluster.json`, `4096`, `15000`, `1000`.

- [ ] **Step 3: Run** `python3 -m pytest tests/integration/test_dashboard_customization.py -q`. Expected: FAIL.

- [ ] **Step 4: Implement `AppStatus`**
  - Uses `QFile`, `QJsonDocument`, `QRegularExpression` and `QDateTime::currentMSecsSinceEpoch()`. It emits `changed` only on difference.
  - Source order: file first; env only when the file does not exist.
  - Display states: `stable`, `trial`, `restored`, `unknown`. `bannerActive == (state == "restored")`.

- [ ] **Step 5: Implement `StatusBadge.qml`** (QtQuick 2.15)
  - A plate anchored bottom-right with 12 px margins, height 64, radius 8, color `#CC111418`, and a 2 px border in the accent color (stable: `#30363D`). Inside it, the title at 22 px bold and the detail at 18 px.
  - The bottom strip: 4 px tall, full parent width, accent color, visible only for `trial`/`restored`.
  - Text and colors come from `status.state`, `status.title` and `status.detail` only.
  - If `status` is null, render `unknown`.

- [ ] **Step 6: Run** the Step 3 command. Expected: PASS.

- [ ] **Step 7: Commit** `feat: add cluster status badge overlay for the Qt dashboard`

### Task 6: Reproducible upstream patch and apply script

**Files:**
- Create: `dashboard/volvo-digital-dash/customization/patches/0001-capstone-ota-status-badge.patch`
- Create: `dashboard/volvo-digital-dash/customization/apply-customization.sh`
- Test: `tests/integration/test_dashboard_customization.py`

**Interfaces:**
- Consumes: Task 5 file names and the `AppStatus` constructor.
- Produces: `apply-customization.sh <imported VolvoDigitalDashModels dir>`, which exits 0 on success and non-zero on a refused or failed application. Marker file: `<dir>/.capstone-customization-applied`.

- [ ] **Step 1: Write failing tests**
  - The patch's `+++` targets are exactly the four allowed files.
  - The patch adds `setContextProperty("capstoneStatus"`, `StatusBadge {`, `<file>StatusBadge.qml</file>` and `app_status.cpp`.
  - The script is executable, uses `set -euo pipefail`, uses `patch -p1 --forward`, and contains the pinned commit hash.
  - A fake tree (a directory lacking `app/main.qml`) → the script exits non-zero and creates no marker.
  - Opt-in `test_patch_applies_to_pinned_upstream`, skipped unless `CAPSTONE_UPSTREAM_DIR` is set. It copies that import to tmp, runs the script (exit 0, marker present, `StatusBadge.qml` copied), then runs it again (non-zero, "already applied").

- [ ] **Step 2: Run** the test file. Expected: FAIL.

- [ ] **Step 3: Generate the patch** from a pristine pinned import:
  - Run `import-upstream.sh` into the scratchpad, copy the tree, and make the four edits from spec §4.3.
  - The `main.qml` edit adds `StatusBadge { anchors.fill: parent; status: capstoneStatus; z: 100 }` after the `warningLightBar` `Loader` inside `gaugeItem`.
  - The `main.cpp` edit adds `#include <app_status.h>`, plus `AppStatus * capstoneStatus = new AppStatus(&app);` and the context property right after the `keyPressEmitter` line.
  - The `app.pro` edit adds `src/capstone/app_status.cpp`, `inc/capstone/app_status.h` and `INCLUDEPATH += inc/capstone`.
  - Produce the patch with `diff -ruN` from the parent of `app/`, so that it applies with `-p1` inside the import dir.

- [ ] **Step 4: Implement `apply-customization.sh`**
  - Validate that the directory exists, that `app/main.qml` and `app/app.pro` exist, and that there is no marker.
  - Run `patch --dry-run` first, then copy `overlay/.` with `cp -R`, then `patch -p1 --forward`, then write a marker containing the pinned commit.

- [ ] **Step 5: Run** the test file normally, then with `CAPSTONE_UPSTREAM_DIR=<scratch import>`. Expected: PASS, and the opt-in test PASSes locally.

- [ ] **Step 6: Commit** `feat: apply cluster status badge to pinned upstream via patch`

### Task 7: Documentation and final verification

**Files:**
- Modify: `docs/ZONAL_OTA_GUIDE.md` (§4 install commands, §5 contract note, §7 demo row and evidence, §8 status rows)
- Modify: `dashboard/volvo-digital-dash/UPSTREAM.md` (the local-change policy now includes an overlay plus a 4-file patch)
- Modify: `README.md` (one paragraph in the Zonal section)
- Modify: `docs/RPI_VALIDATION_CHECKLIST.md` (badge visible on camera; gauges/RPM/gear/warnings unchanged; 1.0.0→1.1.1→1.0.0 sequence; build and run `qt-tests`)

- [ ] **Step 1: Write the docs**
  - §4 adds the `install` lines for the new unit and the tmpfiles conf, plus `systemd-tmpfiles --create` and `systemctl enable --now capstone-ota-ui-status`.
  - The demo row adds `cat /run/capstone-ota-ui/digital-cluster.json` to the evidence.
  - §8 marks the helper, units and patch as executed (A), the Qt build and screen as **not run**.

- [ ] **Step 2: Run** `python3 -m pytest -q`. Expected: `651 + new` passed, 0 failed. Then run `git diff dca36a8 --stat -- capstone_ota/coordinator capstone_ota/common capstone_ota/agent/slots.py capstone_ota/agent/zonal.py capstone_ota/agent/zonal_cli.py tests/unit/test_slots.py tests/integration/test_zonal_end_to_end.py tests/integration/test_zonal_recovery.py`. Expected: empty output.

- [ ] **Step 3: Commit** `docs: describe cluster status badge install, demo and limits`
