# Field Monitor + Deferred Rollback Implementation Plan (CPU contention only)

> **갱신 안내(2026-11-05)**: 이 문서는 초안이며 구현과 달라진 부분이 있다(수습 기간은 기존 trial 단계로, IVI V2는 지도 없음/V3가 지도 추가,
> 한 모니터 콕핏, 안전 창 판단 변경, 계약·정적 게이트는 미구현). 최종 내용은 `docs/IVI_FIELD_MONITOR_RESULTS_2026-11-05.md`와
> `docs/FAILURE_LOG_2026-11-05.md`를 본다.


> Execute task by task with RED/GREEN tests. Hardware tasks remain explicit opt-in.
> Gate 0 decides whether the IVI contention effect is real before any app work.

**Goal:** Pi3 passively monitors a probation window after an IVI OTA, confirms timing
anomalies caused by CPU contention with explainable detectors, and requests rollback only
inside a safe window.
**Architecture:** Existing signed A/B Device + remote-gate; new monitor package (offline-first);
cluster echo frame on CAN; signed contract with tighten-only merge. Central is not updated.
**Tech:** Python, cantools (DBC), Qt5 C++/QML, SocketCAN, systemd, pytest, can-utils, Allure.
**Spec:** ../specs/2026-11-05-field-monitor-rollback-design.md

## Constraints and review focus
- No secrets, private keys or hotspot passwords in repo/artifacts.
- No automatic demo runs; hardware stays opt-in via `--hardware-config`; sequential only.
- Rollback authority: Pi3 requests, Pi2 re-checks safe window itself; absent evidence fails closed.
- Release rules may only tighten vehicle baseline; any relaxation rejects the whole contract.
- Every claim backed by measured numbers; unmeasured or skipped items reported as such.
- Synthetic drive data and time-compressed demos are labeled as such; no OEM-incident claims.
- System changes on boards (installs, services, kernel/network config) need explicit approval.
- CPU load is a normal IVI feature (tile preload, animation), never a planted busy loop.

## Tasks
- [ ] 0. **Gate 0 measurement.** Read-only Pi2 survey (`free -m`, `nproc`, `uptime`, Qt version,
  PSI support, running services, `get_throttled`). With approval, run controlled CPU load
  (`stress-ng` or equivalent, short bursts) while the cluster is fed a driving profile;
  record p50/p99 latency and stalls. Exit: written result decides go/no-go and load size.
- [ ] 1. **Drive cycle generator** (`bench/drive_cycle.py`). Load UDDS/HWFET/WLTC speed files
  (provenance + license note), 10 Hz interpolation, accel limit, gear schedule, rpm model.
  RED/GREEN: bounds, continuity, stop count, determinism. Replace constant-65
  `bench/demo_source.py` with profile playback (keep old behavior test).
- [ ] 2. **Trace tooling** (`capstone_ota/monitor/trace.py`). Parse candump logs with kernel
  timestamps; DBC decode via cantools; pair 0x200/0x300 by counter. RED/GREEN on fixtures.
- [ ] 3. **Detectors offline** (`monitor/metrics.py`, `detect.py`, `classify.py`). Latency
  p50/p99 on change segments, freeze length, skipped updates, EWMA/CUSUM, drift slope,
  signature classifier (delay / freeze / input anomaly). Synthetic traces for each class and
  normal; calibrate on one cycle, evaluate on another. Exit: detects seeded faults, zero
  false confirms on normal traces, detection latency measured.
- [ ] 4. **Contract + signing.** Extend `bundle.metadata_check` for optional
  `contract_sha256` (old releases still valid); `contract.py` schema, resource_budget static
  gate, tighten-only merge with vehicle baseline; Pi3 verifies with its own public key.
  RED/GREEN including relaxed-contract rejection and tampered contract.
- [ ] 5. **Cluster echo frame.** `main.cpp` sends 0x300 (displayed speed, source counter,
  valid, start id) on the same can0; keep read filter; restricted-user TX permission
  documented. Verify ID not in use. RED/GREEN via loopback/vcan test; rebuild for Pi2.
- [ ] 6. **Probation state machine** (`monitor/verdict.py`) + Device hooks:
  provisional commit, `request_rollback(reason, evidence_id)`, Pi2-side safe-window check,
  heartbeat-loss => unmonitored, probation end => final. Reuse existing recovery and
  tree-hash/SVN protections; unit tests for every transition and fail-closed path.
- [ ] 7. **IVI app V2/V3** (after Gate 0 passes). Qt Quick IVI as separate service/user/A-B
  slot with its own device root; V2 light, V3 tile preload (pool=idealThreadCount) +
  animation; state observer socket; deliberately loose cgroup isolation documented. Check
  whether `ClusterDevice`/`Device` can manage a second app slot without core changes.
- [ ] 8. **Pi3 live monitor** (`bench/monitor_service.py`). Passive CAN receive (listen-only
  if root path approved), heartbeat, freeze frames, verdict feed to Pi2 updater; report JSON
  with rule ids and evidence. Hardware test via existing opt-in fixture.
- [ ] 9. **Evidence + metrics.** Load-level sweep (0/25/50/75/100%) x cycles, latency and
  false-positive measurements over soak runs, threshold sweep, rollback-recovery comparison,
  traceability table, golden traces committed (only if license allows), Allure/JUnit;
  extend HDMI panel with state, score, rule id.
- [ ] 10. **Review + docs.** Fresh full review, fix Important findings via RED/GREEN, full
  suite, real-hardware verification, restore normal final state, update README/runbook and
  honest limitations. Commit only on request.

## Verification checkpoints
- After 0: documented decision whether contention is measurable on the target hardware.
- After 3: detector report on traces is reproducible without hardware.
- After 6: state machine and rollback gating fully unit-tested.
- After 8: one real-board run reaches suspect -> confirmed -> wait -> rollback -> recheck.
- After 9: measured tables exist; anything unmeasured is listed as not run.
