# Findings: Debug-port rollback-swap defense

## Scope lock
App-directory A/B OTA only. OS-partition / Yocto / RAUC work discussed earlier in the
conversation is explicitly OUT of scope for this plan.

## Major finding: the core defense already exists in `capstone_ota/device.py`

Read `bundle.py`, `device.py`, `probation.py`, `ivi.py`, `cluster.py`, `process.py`,
`bench/physical.py`, `bench/verify_service.py`, `demo/ivi_attack.py`.

- `bundle.py` already carries an SVN (security-version-number) field in release
  metadata (`metadata_check`: `svn` is a positive int, separate from `version`), and
  `tree_hash(root)` computes a deterministic SHA-256 over an installed slot's file
  names/modes/sizes/contents (binds recovery to the *actual installed bytes*, not just
  a signature).
- `device.py Device._load()/_save()` keep a durable journal (`state.json`) with
  `committed = {slot, release(version/svn/sha256), tree_sha256}` and a monotonic
  `min_svn` floor. This journal record is the "pinned expected recovery" the project
  brief asked for — it already exists, we don't need to invent a new schema.
- `Device._verify_record(record, floor)` (device.py:273-278) is the exact-match gate:
  rejects if `record.release.svn < floor`, and rejects if
  `tree_hash(actual slot on disk) != record.tree_sha256`. This is signature-independent
  exact-identity pinning, not "is this signed by someone we trust" — matches the
  "compare against what we committed, not just 'is it validly signed'" principle from
  the user/mentor discussion.
- `Device._recover()` (device.py:280-310) calls `_verify_record(previous, min_svn)`
  **before** calling `_activate(previous["slot"], state)` (which is what would actually
  run the recovery slot's binary). On mismatch it raises, state becomes
  `recovery_failed`, and the tampered slot is never executed. **This is already the
  "verify immediately before executing the recovery slot" defense the plan is for.**
- `ClusterDevice._activate()` (cluster.py) additionally runs an external verifier
  command (Pi3/Probation) with up to `verifier_timeout` seconds (60s for
  `ClusterDevice`, 3600s for `IviDevice` — ivi.py), both on the trial candidate
  ("verify" stage) and, if recovery is attempted, on the recovery slot ("recheck"
  stage, `recovering = slot == committed.slot`).
- `probation.py Probation.run()` is Pi3's CPU-contention detector wired to this
  verifier slot: it polls Pi3's verdict, requires a `confirmed` anomaly, waits for a
  safe (stopped) window via `_rollback()`, and returns `passed: False` — this failure
  is what makes `ClusterDevice._activate` raise, which is what makes `Device.update()`'s
  except-block call `self._recover(self._load())`.

### End-to-end trace of the already-existing scenario
```
update() installs V3 into the inactive slot, phase="trial", saves journal
  -> _activate(V3 slot): runs app self-check, then external verifier (Probation)
       -> Probation.run() polls Pi3; CPU contention -> Pi3 confirms anomaly
       -> Probation._rollback() waits for a stopped/safe window, returns passed=False
  -> _activate raises ValueError("Cluster external verification failed")
  -> update()'s except calls self._recover(self._load())
  -> _recover(): phase is still "trial" -> previous = state["committed"] (V2)
       -> _verify_record(previous, min_svn)   <-- THE DEFENSE POINT
            - tree_hash(slot A on disk) vs previous.tree_sha256 (pinned at V2's own commit)
            - if attacker swapped slot A's contents (debug port) -> MISMATCH -> raise
       -> only if verify passes: _activate(previous["slot"], state) actually runs V2
```
So "IVI V3 CPU 경합 → Pi3 감지 → 정차 확인 → V2 롤백" is already implemented end to
end, including the "검사해서 다른 이미지라는 걸 발견하면 막는다" defense step
(`_verify_record`), **before** this task starts any coding.

### What is genuinely missing / the real gap for this task
1. **Attack delivery vector.** The existing attack fixtures
   (`demo/ivi_attack.py:attack_check`, `bench/verify_service.py:substitute_old`) swap
   the recovery slot by calling a *trusted, in-process Python function* with direct
   filesystem access (invoked as part of the bench's own SSH'd-in verifier process).
   There is no standalone, always-open, unauthenticated network service standing in for
   the mentor's "리콜 정비 때 열어두고 안 닫은 디버그 포트". This is new code to write.
2. **Combining CPU contention with the slot-swap attack on one timeline.** Per the
   project's own README notes: "CPU 경합과 공격을 합친 실보드 시험은 아직 하지 않았어."
   `bench/verify_service.py`'s existing attack fixture *fabricates* the failing verdict
   (`ATTACK_FIXTURE_ROLLBACK_REQUEST`) instead of letting genuine CPU-contention-driven
   latency (bench/load_phases.py + Pi3's p99 detector) be what triggers the rollback.
   This plan should let real contention trigger it, with the slot swapped independently
   and earlier (while V3 is still in its trial/probation window) via the new debug port.
3. **No demonstrated "vulnerable mode".** There's no toggle today to turn OFF
   `_verify_record`'s tree_hash/SVN check for a side-by-side defended-vs-vulnerable demo.
   Needs a narrowly-scoped, loudly-labeled flag (never default-on, not reachable over
   the network) purely for the contrast demo.
4. **Trust-boundary enforcement is currently by convention/test-assertion only.**
   `verify_service.py:substitute_old` asserts `state.json` is byte-identical
   before/after as a test-time sanity check, but nothing *structurally* stops a write
   path from touching `state.json`/`trusted.pub`/`current`/`.lock`. The new debug-port
   service must enforce this by construction (its write path only ever resolves inside
   `root/slots/<name>/`, nothing else), not just assert it after the fact.
5. **Old-key-not-revoked angle (K1/K2) — optional stretch.** `bundle.py:verify()` takes
   one `public_key: Path`; `device.py` pins exactly one `trusted.pub` for the whole
   device lifetime and never rotates/revokes it. Modeling "K1 was never revoked after
   rotating to K2" would need a real trust-list extension (not in scope for the
   baseline defense — the exact-hash pin in `_verify_record` already defeats the attack
   regardless of which key signed the planted image). Mark as optional/deferred stretch
   phase; do not block the core plan on it.

## Real-hardware harness already in the repo (reuse, don't rebuild)
- `bench/physical.py: PhysicalBench` — SSH-based orchestration across
  central(Pi1)/cluster(Pi2)/verifier(Pi3) peers. Has `new_device(..., attack=bool)`
  and `scenario(name in {'compatible','attack',...})` already combining forward() OTA
  calls with the verify_service.py attack fixture. New debug-port scenario should add a
  new `scenario` name / method here rather than a parallel harness.
- `bench/load_phases.py` — the existing CPU-contention generator ("N busy processes",
  matches project's "Python 계산 프로세스 4개... 인위적인 자원 경합 시험"). Reuse
  directly for the combined CPU-contention + attack timeline.
- `bench/verify_service.py` — trusted bench-side checker run via SSH on Pi2; already
  knows how to reach Pi3 (`--verifier`) and has the `substitute_old` pattern to crib
  the "replace slot with a different validly-signed release" logic from (but must be
  re-delivered through the new unauthenticated debug-port TCP service instead of being
  called in-process by the trusted checker).
- `bench/evidence.py: Captures` — existing evidence/log capture helper for bench runs;
  reuse for before/after-rollback evidence in the combined demo.
- `tests/hardware/test_physical_can.py` — pytest entry point over `PhysicalBench`;
  real-hardware-only (skipped without boards), matches README's "실보드 전용 9개
  제외" note.
- `demo/ivi_attack.py` + `demo/run.py` (`serve()`) — the local (non-hardware,
  two-hop-HTTP, SSH-substituted) equivalent demo. A software-only version of the new
  scenario should extend this file's pattern for fast iteration before running on real
  Pi hardware.

## Decisions / open questions resolved during research
- Reuse `state.json`'s existing `committed.tree_sha256` + `min_svn` as the pinned
  "expected recovery" record; do NOT invent a parallel `last_known_good` field — the
  journal's `committed` record already plays that role for the in-flight trial/rollback
  window this scenario operates in (V3 is never actually marked `committed` until its
  probation passes, so V2's record is still `state["committed"]` the whole time the
  attack window is open).
- Signature re-verification at rollback time is NOT needed in addition to tree-hash
  pinning: the codebase's trust model checks the Ed25519 signature once, at download
  time, then pins the resulting hash forever after via `tree_hash`/`_verify_record`.
  Mirroring that design (rather than re-checking a signature that isn't even stored
  inside the extracted slot) keeps the new code consistent with the rest of the
  project.
