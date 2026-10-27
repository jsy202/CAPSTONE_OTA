# Cluster relay implementation plan

> Execute inline with superpowers:executing-plans. User already authorized autonomous implementation.

**Goal:** Forward a verified release through Central and install/check/rollback on Cluster.
**Architecture:** Verified temporary HTTP cache plus strict-host-key SSH; reuse Device on target.
**Tech stack:** Python standard library, existing cryptography and pytest.
**Spec:** docs/superpowers/specs/2026-10-27-cluster-relay-design.md

## Global constraints

- No changes to OS, existing Qt service, signing-key distribution or old worktrees.
- Exact V2 recovery identity/floor remains enforced; no service restore after refusal.
- Explicit initialization-only option or trusted external verifier, never silent fallback.
- LAN HTTP is authenticated content transport only; no confidentiality or availability claim.

## Review focus

- Corrupt relay bytes or wrong target key must not install.
- Target refusal, missing/false/malformed verdict and SSH failure must not report success.
- Interrupted trial recovery must use the target and its external gate without downloading.
- Server must expose only three cache files; no root listing or private files.
- Timeouts after remote commit are uncertain outcomes, not proof that installation failed.

## Task 1: Target installation and shared process runner

- [x] Write failing tests for Cluster CLI installation, explicit mode, external verdict rollback and V1 replacement rejection.
- [x] Move existing bounded exchange into capstone_ota/process.py, preserving demo import compatibility.
- [x] Add capstone_ota/cluster.py with ClusterDevice activation gate and fixed Cluster service.
- [x] Add cluster-update CLI and test real subprocess outcomes; run full suite.

## Task 2: Verified two-hop forwarding

- [x] Write failing tests for actual two-hop HTTP, source shutdown, corruption, fixed serving paths, target failure.
- [x] Add capstone_ota/relay.py and forward CLI with a small target JSON configuration.
- [x] Verify explicit state/result from SSH target; preserve rollback result and uncertainty on transport errors.
- [x] Add Cluster service example, configuration and deployment guide; update README/architecture status.
- [x] Run full suite, get one independent branch review and fix meaningful findings; ready for authorized commit/push.

## Execution record

- Baseline: 68 tests passed before changes; clean feature/virtual-compatibility-demo.
- Pi hostname does not resolve on this run. Hardware deployment is unavailable; local real-process/two-hop verification continues.
- Initial RED: nine new tests failed because target CLI/relay were absent. Subsequent full suite: 87 passed.
- A generic v1/v2 fixture had identical app bytes; replaced the attack fixture with a truly different signed old payload and proved it was not executed.
- Actual end-to-end forwarding exposed pretty-printed JSON vs JSON-line control mismatch; Cluster CLI now emits one machine-readable line.
- RED→GREEN: known recovery refusal is returned explicitly; wrong committed security floor is not accepted as success.
- Independent review found empty --check-command could silently bypass external validation. Regression reproduced a committed installation; fixed with an explicit None check. No other Critical/Important findings.
- Ruling: existing Qt payload preparation/model telemetry is not included in this forwarding change — no existing Qt service or app source is modified; this is not a claim of completed screen verification.
- Final: **88 passed in 36.55s**. Review regression RED→GREEN; no remaining Critical/Important findings. Wheel built successfully and installed in an isolated directory; CLI checked outside the source checkout. HTML status notice checked at mobile width without overflow.
