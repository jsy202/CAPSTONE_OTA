# Findings: CI/CD pipeline

## CI already existed
`.github/workflows/tests.yml` ran pytest on push/PR with Python 3.10/3.12, coverage,
JUnit and Allure artifacts. No lint, no Qt build, no release/CD.

## Why main was red (run 43161659485, commit cdb3cba)
- Log: `FAILED tests/test_local_cockpit_replay.py::test_local_preview_is_isolated_and_keeps_recovery_refusal_last - ModuleNotFoundError: No module named 'PIL'`
  -> `1 failed, 242 passed, 47 skipped`.
- Root cause: workflow installed `.[verification]`; Pillow is in the separate `cockpit`
  extra in pyproject.toml. The cockpit replay test (added in 8c4027a / cdb3cba) imports PIL.
- Reproduced in a clean venv: without Pillow -> same failure; with `.[verification,cockpit]` -> passes.

## Green did not mean Qt was tested
- Without the compiled apps, every test in `test_qt_cluster.py` and `test_qt_progress.py`
  skips (`build the actual Qt dashboard first`, `build the native progress panel first`).
  In CI-like conditions locally: 27 Qt skips out of 45 total.
- With the apps built (pinned upstream + Qt 5.15) the same files give 29 passed, 0 skipped.
- Upstream: https://github.com/whitfijs-jw/Volvo240-DigitalDash at
  793452919127065536bcb7a08f98838fa963d75e (PIN in dashboard/qt_cluster/build.py).
  Cluster build takes ~11s.
- QML modules needed (from the upstream app's imports): QtQuick, Controls 2, Window,
  Shapes, Layouts, GraphicalEffects.

## Other silent skips
- 8 tests skip with `vcan0 is not available in this environment` (e.g.
  tests/test_cluster_can_verdict.py). Not fixed: loading the vcan module on the hosted
  runner was not verified, and enabling it unverified could turn main red.
- 10 hardware tests skip without `--hardware-config` (intended).

## Lint baseline
- `ruff --select E9,F63,F7,F82` (syntax errors, undefined names): clean -> made blocking.
- F401 (28) / F841 (1): advisory only.
- F811 (96): almost all pytest fixture parameters shadowing imported fixtures (false
  positives) -> not enabled.

## Test-case spec (docs/TEST_CASES.md) covers hardware tests only
- 15 TC rows (CAN-*, OTA-CAN-002-*, SEC-003/004, NET-001), all mapped to
  tests/hardware/test_physical_can.py. The ~270 software tests carry no TC IDs.
- Drift found: `test_SEC_005_debug_port_cannot_smuggle_a_rollback_under_real_cpu_contention`
  and `test_SEC_005_missing_third_board_can_witness_cannot_commit` have no TC row.
  Not filled in: expected results would have been guessed.
- CAN-003-06 and CAN-004-EXT name no function in the spec -> reported as `no-function`.

## Release design
- Reuses `capstone_ota.bundle.pack/verify/tree_hash` and `dashboard.ivi.build.payload`
  (same functions bench/ivi_release.py uses), no new bundle format.
- `release/versions.json` maps version -> label/SVN (2.0.0 -> V2/svn1, 3.0.0 -> V3/svn2,
  same as bench/ivi_release.py RELEASES).
- Signed release is re-verified with its own public key before it can be published;
  build-info.json and SHA256SUMS sit next to (not inside) the signed files.
- Tag push without `OTA_SIGNING_KEY` fails closed; workflow_dispatch uses a throwaway key
  and never publishes.
