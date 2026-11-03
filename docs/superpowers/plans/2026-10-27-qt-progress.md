# Qt Progress Implementation Plan

> Execute inline with superpowers:executing-plans.

**Goal:** Independent Qt progress beside real dashboard, using actual updater events.
**Architecture:** Best-effort atomic telemetry outside slots; read-only native Qt observer.
**Tech Stack:** Python stdlib, existing Qt 5.15 Quick/C++17.
**Spec:** ../specs/2026-10-27-qt-progress-design.md

## Constraints
- Never change security decisions or let UI telemetry errors fail an update.
- Byte percentage only when length is known; no fabricated aggregate percentage.
- Preserve visible last mismatch through rollback; never claim recovered on refusal.
- Do not alter existing Pi service; no private keys in repository.

## Tasks
- [x] 1. RED: real HTTP update telemetry, actual transfer totals, successful rollback,
  tampered recovery refusal and unwritable telemetry. Implement stage snapshots.
- [x] 2. RED: native panel consumes missing/live/error snapshots and captures actual
  QML output. Build separate executable with existing Qt; add read-only viewer.
- [x] 3. Connect live PC demo display, document launch, capture side-by-side evidence.
- [x] 4. Full tests, fresh review, fix important findings, commit on the feature branch for integration by the parent task.

## Review focus
- Interrupted writer/partial data must never become successful progress.
- Repeated update must clear old mismatch while rollback must retain current mismatch.
- Unknown/incorrect Content-Length must not create false completion.
- Failed recovery must not label committed metadata as an active healthy app.
- UI must survive candidate termination and remain outside payload slots.


## Completion record — 2026-11-03

- Tasks 1–3 implemented: observational atomic telemetry, separately built C++/QML
  viewer, live single-scenario PC presentation and captured side-by-side evidence.
- Fresh review already completed before this resumed verification. Both Important
  findings were fixed with failing-then-passing regression tests: terminal rollback
  display uses best-effort `_progress` rather than a new authoritative event write;
  `DownloadDeadline` propagates through telemetry's `OSError` handler.
- Added six actual-QML lifecycle cases for malformed/unknown records, stale/future
  timestamps, dead updater and retained terminal success. Native panel build passes;
  all 10 native panel cases pass (4.63s).
- Inspected the existing live V2 capture and companion mixed/attack JSON evidence;
  retained expected 62 / actual 45 survives completed rollback and recovery refusal.
- Build/runtime/presentation commands and evidence limits: `docs/QT_PROGRESS.md`.
- Ruling: commit only to the existing feature branch, without pushing main or
  merging — current integration instructions supersede the original plan's final
  push instruction. Parent task owns integration; no hardware action is included.
- Fresh full suite after lifecycle additions: `.venv/bin/python -m pytest -q`
  **120 passed in 97.05s**, zero skipped or failed. Native Qt scenario and panel
  tests executed; log: `.demo/qt-progress-pytest-2026-11-03.log` (ignored local artifact).
  The first resumed full run before the additions was also green: 114 passed in
  95.38s. `git diff --check` passed.
- No new production fix was needed in the resumed lifecycle inspection. This work
  does not claim an ARM panel build, physical Pi display verification or telemetry
  transport verification; those require separate bench evidence.
