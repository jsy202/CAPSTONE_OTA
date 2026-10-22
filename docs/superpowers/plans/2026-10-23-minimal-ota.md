# Minimal application OTA implementation plan

> For agentic workers: use superpowers:executing-plans inline, test-first, then one independent whole-branch review.

**Goal:** Replace the default multi-path OTA experience with one understandable single-device download/verify/trial/commit-or-recover flow, and push a separate branch.

**Architecture:** Three small modules: signed packages, durable A/B device state, and a CLI. An HTTP file server transports untrusted bytes; a pinned Ed25519 public key verifies three-field release metadata and its archive hash. The fixed application entrypoint is `bin/app`, with an application-owned `--check` initialization check. Optional fixed systemd service integration restarts a persistent application; local mode only installs and self-checks it.

**Tech Stack:** Python 3.10+, standard library, cryptography, pytest. Linux for process locks and systemd integration.

**Spec:** This document records the user-approved simplification, not a new compatibility or attack scenario.

## Global Constraints

- Preserve existing dirty checkouts and the running Pi; no OS/service migration.
- Preserve old tracked code/config/tests/documentation under `legacy/`; exclude it from the new installed package/default tests.
- One OTA target, one state journal, application-directory A/B only. No MQTT/CA/client-cert setup in the new path.
- Keep signature/hash verification, bounded safe extraction, local crash recovery, and a monotonic security floor.
- No new CAN/Qt integration, generic contracts, independent verdict protocol, vulnerability demo, or OS A/B.
- Commit and push `refactor/minimal-ota`; do not merge main or publish secrets.

## Review Focus

- Interrupted trial must never become an implicit successful commit; recovery must recheck the previous tree and application.
- State corruption, symlink escapes, unsafe tar entries, and oversized downloads must fail before executing an unexpected file.
- Authentication must precede use of signed fields; a provided new key must not replace an initialized device's pinned key.
- Concurrent update/recovery processes must not race slot selection; status must remain read-only.
- Local self-check is not a claim of a managed persistent process, independent CAN correctness, or hardware-backed antirollback.

### Task 1: Archive the old surface and implement signed packages

**Files:** Move old tracked implementation/config/tests/docs to `legacy/`. Create `capstone_ota/bundle.py`, `tests/test_bundle.py`, minimal package/dependency metadata.

**Interfaces:** `pack(payload, output, version, svn, key)` produces `release.json`, `release.sig`, `app.tar.gz`; metadata has exactly `version`, `svn`, `sha256`. `verify(raw, signature, public_key)` returns validated metadata; `extract(archive, destination)` rejects unsafe/bounded archives. Fixed executable `bin/app` required.

- [ ] Write tests for real key/package verification, tampering, three-part numeric version validation, bad SVN, unsafe archive paths/links and non-executable entrypoints. Run and watch failure before adding implementation.
- [ ] Archive legacy files mechanically; implement the minimal package API without new abstractions.
- [ ] Run `python -m pytest tests/test_bundle.py -q`; require all passing; commit task.

### Task 2: Implement one device state machine and CLI

**Files:** `capstone_ota/device.py`, `capstone_ota/cli.py`, device/CLI tests, example Central payload.

**Interfaces:** Device `update(url=None, public_key=None, systemd=False)` downloads a fixed three-file release directory or, without URL, recovers an interrupted trial. `status()` only reads. `ota pack`, `ota update [URL]`, `ota status`; shared `--root` selects an isolated device directory. Pinned key and state live outside slots. Bootstrap requires an explicit key; first successful release occupies A, later trials alternate B/A. The trusted journal stores committed release/tree digest, pending trial, service mode and minimum SVN; a single atomic commit promotes release and floor. Recover reverts uncommitted trials, checks committed tree and readiness, and reports failure without claiming recovery success when unusable.

- [ ] Write device tests with real signed HTTP packages and actual executable fixtures: first install, alternate slots, failed trial recovery, tampering rejection, below-floor rejection, interrupted trial, corrupted recovery image, pinned-key replacement, read-only status and update serialization. Watch expected failures.
- [ ] Implement bounded downloads, exclusive Linux root lock, safe staging, durable state and selector writes, process self-check, optional fixed `capstone-central.service` restart, and CLI error exits.
- [ ] Add a small example app whose `--check` exercises the same config initialization as normal execution. Tests run CLI subprocesses, not text inspection.
- [ ] Run default full suite and executable local smoke; commit task.

### Task 3: Document the single workflow and ship the reviewed branch

**Files:** Root README, one Pi setup guide, one systemd unit, CI workflow and legacy notice.

- [ ] Document keys once, pack/HTTP serve/update/status, initial install and failed-update recovery; explicitly describe HTTP and local self-check limits and no Pi migration.
- [ ] CI installs only the new default package, exercises `ota --help`, and runs the full new suite. Preserve historical tests in legacy and document how to run them separately.
- [ ] Independently review whole branch with plan and ledger. Fix important findings with failing regression tests, then run the full suite, installed-package smoke, build and secret/diff checks.
- [ ] Commit docs/fixes, push the named branch, verify its remote SHA and report tests, branch URL and deferred work. No main merge.
