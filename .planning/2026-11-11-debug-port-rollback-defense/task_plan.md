# Task Plan: Debug-port recovery-slot swap vs. pinned-rollback defense

## Goal
Demonstrate, in software first and then on the real Pi1/Pi2/Pi3 rig, that an
always-open recall-service debug port on Pi2 can tamper with the IVI recovery
(backup) slot while V3 is mid-update, and that the existing commit-time pinned-hash
re-verification (`Device._verify_record`, already in `capstone_ota/device.py`) stops
the tampered image from ever being executed during a CPU-contention-triggered
rollback — with a clearly-labeled "vulnerable mode" toggle to show the same attack
succeeding when that re-verification is disabled. App-directory A/B OTA only; no
OS-partition/Yocto/RAUC work (explicitly deferred, out of scope for this plan).

## Next Step
Phases 1-7 complete. Remaining: (a) run Phase 6 against real boards once reachable
and confirmed, and smoke-test it carefully; (b) decide whether to start optional
Phase 8 (old-key/K1-revocation modeling) -- not started, not required for the
defense to work.

## Current Phase
Phase 7 (all required phases complete; Phase 8 optional/not started)

## Phases

### Phase 1: Requirements & Discovery
- [x] Capture the mentor/user scenario (recall debug port left open; V2→V3 update;
      attacker swaps recovery slot via the port; CPU contention triggers Pi3-driven
      rollback; defense must catch the swap *before* executing the recovery slot)
- [x] Read `bundle.py`, `device.py`, `probation.py`, `ivi.py`, `cluster.py`,
      `process.py`, `bench/physical.py`, `bench/verify_service.py`,
      `demo/ivi_attack.py` and confirm how much of this already exists
- [x] Document findings and the precise gap in findings.md
- **Status:** complete

### Phase 2: Vulnerable-mode toggle (defense off/on switch for the demo contrast)
- [x] Added `Device(root, recovery_verification=True)`; `ClusterDevice`/`IviDevice`
      pass it through. `_verify_record` skips its tree-hash/SVN check and logs
      `VULNERABLE_MODE_RECOVERY_UNVERIFIED` when off. Never default-off, never
      settable from network input.
- [x] Unit test `test_debug_port_style_slot_swap_is_blocked_before_activation`: a
      hand-tampered committed slot makes the very next `update()` call raise
      `ValueError("...tree hash mismatch")` at the top of `update()`'s own recovery
      self-check, before any download or `_activate()` — stronger than planned: the
      defense fires even without a second trial attempt.
- [x] Unit test `test_vulnerable_mode_toggle_actually_disables_the_block`: same
      tamper, `recovery_verification=False` — the tampered app is activated and
      `result == "rolled_back"` (proves the toggle is not a no-op).
- [x] Full non-hardware suite re-run: 260 passed (258 baseline + 2 new), no
      regressions from the constructor signature change.
- **Status:** complete

### Phase 3: Always-open debug/maintenance port service (new attack delivery vector)
- [x] `capstone_ota/debug_port.py`: stdlib-only `ThreadingTCPServer`, no auth, always
      listening once started. One capability: read a slot name + size-prefixed
      tar.gz off the socket and extract it (via `bundle.extract`, the same safe
      extractor the real pipeline uses) to replace that slot's directory.
- [x] Structural enforcement via `slot_path()`: resolves and requires the target be
      exactly `root/slots/A` or `root/slots/B`; nothing else in the module ever opens
      another path, so `state.json`/`trusted.pub`/`current`/`.lock` are unreachable
      by construction, not by convention.
      — enforced by the service's own path-join/realpath check, independent of caller
      input
- [x] `tests/test_debug_port.py` (3 tests): `slot_path` rejects every escape
      attempt tried (`C`, `..`-traversal, empty, `state.json`); `push()` replaces a
      slot's contents end to end; an invalid slot name is rejected and leaves
      `state.json`/`trusted.pub`/the untouched slot byte-identical.
- **Status:** complete

### Phase 4: Attack client + fixture release
- [x] No separate script needed: `debug_port.push(host, port, slot, archive_path)` IS
      the attack client (reused directly in Phase 3's own tests and Phase 5's
      scenario) — a `--when` hook was dropped because the combined scenario fires it
      from inside the fake Pi3 verifier process at exactly the trial-window moment
      instead of a timed script, which is both simpler and more realistic than
      sleep-based timing.
- [x] `_planted_archive()` in the Phase 5 test builds the validly-signed-but-different
      release via `bundle.pack()`, same as `demo/ivi_attack.py`'s existing pattern.
- **Status:** complete

### Phase 5: Combined software demo (no real hardware yet)
- [x] `tests/test_debug_port_rollback_scenario.py`: a fake Pi3 verifier subprocess
      blesses the V2 baseline, then on the V3 trial call it fires the debug-port
      attack against slot A *before* reporting a confirmed anomaly, then (only in
      vulnerable mode) is asked again to bless the now-planted recovery slot.
      (Running the V3 trial in a background Python thread was tried first and
      rejected: `device.py`'s download timeout uses `signal.SIGALRM`, which only
      works on the main thread -- logged in Errors Encountered.)
- [x] `test_defended_rollback_blocks_the_planted_recovery_image`: raises
      `"recovery failed..."` (cause: `"...tree hash mismatch"`), the external
      verifier is asked exactly twice (never asked to bless the plant), `current`
      stays on the stuck V3 trial slot B, and the plant sits unexecuted on disk in A.
- [x] `test_vulnerable_mode_lets_the_planted_image_through`: no exception, verifier
      asked a third time and blesses the plant, `current` switches to A, the planted
      marker file is live.
- [x] Both land in the normal pytest run (no hardware marker needed). Also found
      and fixed (ponytail-review pass) a real gap: the debug port had no
      per-connection timeout, so one stalled client could hold a thread forever on
      an "always open" service; added `connection_timeout` (default 10s) and a
      regression test. Full non-hardware suite now **266 passed** (258 baseline + 2
      Phase 2 + 4 Phase 3 + 2 Phase 5).
- **Status:** complete

### Phase 6: Real-hardware integration (Pi1/Pi2/Pi3) — RUN, and now 10/10 passing
- [x] Added `PhysicalBench.debug_port_rollback()` to `bench/physical.py`, following
      `scenario()`'s exact existing idioms.
- [x] Added `test_SEC_005_debug_port_cannot_smuggle_a_rollback_under_real_cpu_contention`
      to `tests/hardware/test_physical_can.py`.
- [x] **Actually run against the real boards** once Wi-Fi was confirmed (separate
      thread in this conversation). Hit and fixed three real, pre-existing
      blockers, none caused by this feature, all logged with full trial-and-error
      in progress.md:
      1. A crash-looping legacy MQTT service on Pi1 (902 restarts) — disabled.
      2. **A real race condition in `dashboard/qt_cluster/main.cpp`**: `--check`
         tested `engine.rootObjects().isEmpty()` exactly once, synchronously,
         right after `engine.load()`; under `offscreen`/software rendering on the
         actual Pi the root QML object was not reliably ready yet, so EVERY real
         OTA/attack/security hardware test (not just this one) failed with a
         silent exit(2), 100% of the time, with no prior board ever having
         exercised this exact code path (per the project's own "최근 단일 클러스터
         정리는 아직 실보드에 배포하지 않았다" note). Root-caused with `gdb` (built a
         `-g` debug variant directly on Pi2, which has `qmake`/`g++` and the pinned
         Volvo checkout already — no cross-compiler needed) by breakpointing each
         `return 2;` site; confirmed by the fact that merely pausing execution
         under gdb made it pass. Fixed by pumping `QCoreApplication::processEvents()`
         for up to 500ms before failing, instead of checking once. Rebuilt
         (debug and release) on Pi2, verified 10/10 clean runs as both root and
         the real `capstone-app` service user before trusting it.
      3. Two stale SSH host aliases left over from the iPhone-hotspot era
         (`capstone-verifier` on Pi2's root config, `capstone-source` on Pi3's
         otauser config) still pointed at `172.20.10.x` — fixed both IPs; this is
         what the 3-board `--verifier` CAN-witness path needs to reach Pi3/Pi1.
      4. My own test's first design assumed a shared verifier-call counter across
         the V2 (default checker) and V3 (custom checker) `forward()` calls, so
         the attack never fired; and the attack archive was root:600 (unreadable
         by the otauser-level process that actually runs `check_command`).
         Simplified to an unconditional one-shot script and staged an
         otauser-readable copy of the fixture archive.
- [x] **Result: `pytest tests/hardware/test_physical_can.py --hardware-config
      .demo/three-board-config-fixed.json` → 10 passed**, including the
      pre-existing `compatible`/`attack`/`missing-evidence`/`missing-witness`
      scenarios that were ALSO blocked by blocker #2 above and are now proven
      working for the first time with the single-fixed-cluster cleanup deployed.
- [ ] Known, explicitly out-of-scope-for-today gap (documented, not hidden): the
      V3-trial failure in `debug_port_rollback()` is forced deterministically by
      its own check_command, the same way the existing `attack` scenario forces
      it — NOT by a genuine `Probation`/`monitor.detect` p99 CPU-contention
      verdict, because that real-time CAN monitor is not deployed as a running
      service on the Verifier board. `bench/load_phases.py` does run alongside
      for a real CPU load, but it does not yet gate the verdict. Wiring
      `Probation` into `PhysicalBench` as the actual trigger is a real follow-up,
      separate from this plan's scope (it needs a monitor service deployed and
      running on Pi3 first).
- [x] New artifact: `.demo/pi-qt-2026-11-11-fixed/capstone-qt-cluster-aarch64`
      (release build with the race fix) and `.demo/three-board-config-fixed.json`
      (points `arm_binary` at it). The original `.demo/pi-qt-2026-11-02/...`
      binary and `.demo/three-board-config.json` are left as-is (historical).
- **Status:** complete — code written, deployed, and passing on real hardware

### Phase 7: Write-up / README update
- [x] Added a new README section ("디버그 포트를 이용한 복구 슬롯 바꿔치기") stating
      the professor-facing sentence, the exact trust boundary
      (`state.json: committed.tree_sha256`/`min_svn`, structurally unreachable by the
      debug port), the vulnerable-mode toggle's scope (demo-only, no network/CLI
      path reaches it, confirmed by code audit below), and the commands to run the
      software scenario.
- [x] Added a bullet to the existing "현재 한계와 남은 일" list noting the real-board
      run is written but not executed.
- **Status:** complete

### Phase 8 (optional / stretch, defer unless time permits): old-key-not-revoked (K1/K2)
- [ ] Only start this if Phases 2–7 are done and there is time left
- [ ] Model a second, superseded signing key K1 that was never revoked, so the planted
      V1 payload is signed by K1 rather than the current key — strengthens the
      "서명이 유효해도 막아야 한다" narrative but is NOT required: `_verify_record`'s
      exact tree-hash pin already defeats the attack regardless of which key signed
      the planted image (see findings.md)
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Reuse `state.json`'s existing `committed.tree_sha256`/`min_svn` as the pinned record instead of inventing a new field | It already plays exactly this role for the in-flight trial window this scenario lives in; V3 never becomes `committed` until probation passes, so V2's record stays `state["committed"]` the whole attack window |
| No signature re-check at rollback time, only tree-hash pin | Matches existing project trust model (signature checked once at download, pinned by hash thereafter); the extracted slot doesn't even carry release.json/sig to re-check |
| New debug-port service is the main new code, not a new defense mechanism | The defense (`_verify_record`/`_recover`) already exists; the project's gap is realistic attack *delivery*, not detection |
| Old-key/K1-revocation modeling pushed to optional Phase 8 | Core attack is already defeated by exact-hash pinning regardless of signing key; added only if time allows, per explicit "OS stuff deferred, keep scope tight" instruction |
| Hardware phase (6) is "ready, not necessarily run" | Board Wi-Fi/connectivity is being worked in parallel in this same conversation; don't block this plan on board reachability |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| First Phase-5 draft ran the V3 trial `update()` in a background thread and polled a sentinel file to time the debug-port attack mid-trial; failed with `ValueError('signal only works in main thread...')` because `device.py:fetch()` uses `signal.SIGALRM` for the download deadline | Dropped threading entirely. Moved the attack trigger into the fake verifier *subprocess* itself (it calls `debug_port.push()` on its first real call, i.e. exactly when "Pi3" is evaluating the V3 trial), which is simpler and timed more precisely than sleep/poll ever was |
| First version of `test_debug_port_style_slot_swap_is_blocked_before_activation` asserted the wrong exception message/that `state.json` would record `"recovery_failed"` | Traced `Device.update()`/`_recover()` by hand: when `phase == "stable"`, `_recover()`'s short-circuit branch calls `_verify_record` **unwrapped** (no try/except, no `_save`), so the real failure is the raw `"...tree hash mismatch"` raised at the very top of the *next* `update()` call, before that call's own URL/download logic ever runs — an earlier and stronger check than the plan assumed. Fixed the assertions to match; noted the stronger-than-expected behavior in findings.md |
