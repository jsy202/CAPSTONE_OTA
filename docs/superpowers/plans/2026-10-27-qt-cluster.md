# Qt Cluster implementation plan

> Execute inline with superpowers:executing-plans; user approved autonomous implementation.

**Goal:** Real Qt payload/model verification, controlled delayed-data fault injection,
and Central→Cluster OTA commit/rollback/V1 refusal on PC.
**Architecture:** Pinned upstream UI + small Qt adapter; external Python verifier,
explicit UDP/CAN transports, existing signed relay/ClusterDevice.
**Spec:** docs/superpowers/specs/2026-10-27-qt-cluster-design.md

## Global constraints
- Preserve upstream Downloads and existing Pi services.
- No fabricated FAIL values inside Qt; faults belong to the independent source.
- Report model/QML binding proof separately from pixels/hardware/week-long endurance.
- Reuse signing, A/B, pinned keys and recovery identity; no new broker/rule engine.

## Tasks
- [x] RED tests for actual Qt startup, frame decode, displayed speed/RPM/gear/warnings,
  stale rejection, CRC rejection and timeout/recovery.
- [x] Reproducible pinned upstream build overlay and immutable Qt payload launcher.
- [x] External source/snapshot/verifier roles with local and strict SSH control.
- [x] Fault profiles and combination matrix, then signed two-hop OTA end-to-end,
  actual V3 trial rejection, V2 recheck and authentic V1 substitution refusal.
- [x] Full suite/build/visual smoke, independent review, meaningful fixes, docs and
  evidence artifacts; ready for authorized commit/push.

## Review focus
- Verifier must read the selected running candidate, not an old process or fake JSON.
- Report counter/process identity and fresh challenge; reject missing/extra evidence.
- Bound UI/control I/O, startup and shutdown; do not unlink someone else's socket.
- Candidate must not mutate signed slots, initialize GPS or overwrite config files.
- Recovery uses the same observer; real service state and trial process cleanup agree.

## Execution record

- Baseline 6f72e7e, 88 Python tests. New missing Qt API tests RED, then real compiled UI tests GREEN.
- Built the pinned Volvo model/QML subset without Eigen/GPIO/GPS. Headers/libraries came from the existing extracted Qt prefix; Downloads originals unchanged.
- Actual repeated startup exposed async QML shutdown segfault. Wait for completed incubation/visible original gauge and destroy engine before models; a 12-run immutable startup test covers this.
- User reported intrusive original joke/splash windows. Neutralized copied QML and moved tests to offscreen/private Xvfb. No user display is accessed by --check.
- Qt observer now reads original Gauge.value, not just the new overlay label. Captures show the original gauges.
- Remote-gate/source/observer command chain runs in real separate processes with a local SSH transport substitute. Regression exposed JSON shell quoting; fixed by quoting the complete source command.
- First full suite: 99 passed; complete signed Qt chain committed compatible V3, rolled mixed V3 back to V2, refused genuine substituted V1.
- Independent review: Important replay trace changed prior counter/payload pairs in later cycles. RED regression reproduced; fixed by retaining original 241/450 and 242/600 frames.
- Ruling: regraded unrelated CAN-ID generation issue as functional for a multi-ECU bus, not cosmetic — fixed with a native receive filter and transport-independent ID guard; actual Qt UDP regression RED→GREEN. Cost: this receiver intentionally observes only its 0x200 vehicle stream.
- Ruling: model/binding telemetry and private rendered captures are the evidence; no optical oracle, malicious signed UI defense, physical CAN/ARM/SSH-auth or actual week-long endurance claim.
- Final: 100 passed in 84.04s; exact replay/other-ID regressions GREEN, package wheel built and executed outside source checkout, independent final demo confirms all three OTA outcomes. Evidence copied to Downloads without keys.
