# Progress Log

## Session 2026-11-16 (trial-and-error log)

### Process
- Read the repo through `gh` (README, tree, existing workflow, recent runs) before
  proposing anything. Found the CI already existed and the latest main run had failed.
- First guessed the failure was Pillow from the extras layout, and said so as a guess.
- User asked to stop and explained the purpose: resume/portfolio. Laid out stages
  (CI fix / Qt cross-build / signed release / HIL) and what would be contrived
  (Docker, cloud deploy, lint gates everywhere).
- User asked about CD: defined it as "build + sign the OTA release", since installing on
  the board is the existing OTA client's job.
- User pasted another assistant's plan. Kept its good parts (tested artifact == shipped
  artifact via commit+hash, TC-ID traceability, self-hosted runner security note),
  flagged its stale/unverified case table and the resume sentence that would overclaim
  if written before implementation. User chose stages 1-3.
- Mistake: started touching code right after "start" without a design doc or plan and
  without the debugging skill. User called it out ("코딩관련 스킬들 왜 사용안해?").
  Switched to systematic-debugging, stated the short design, then the user said to
  finish autonomously overnight.

### Root cause
- `gh run view --log-failed` printed nothing for run 43161659485. Pulled the raw job log
  through `gh api repos/.../actions/jobs/130994067389/logs` instead. Found the
  `ModuleNotFoundError: No module named 'PIL'`. The earlier guess was right but only
  confirmed here.

### Setup snags
- Local `~/CAPSTONE_OTA` was 46 commits behind origin/main with uncommitted user work
  (README.md, demo/, docs/VIRTUAL_DEMO.md, a test). Left it untouched; created worktree
  `~/CAPSTONE_OTA_ci` from origin/main on branch `ci/cicd-pipeline`.
- `git commit` failed: no author identity. Set repo-local user.name/email to the
  identity used by existing commits (jsy202 noreply), did not touch global config.
- `git push` rejected: `refusing to allow an OAuth App to create or update workflow
  ... without workflow scope`. Token had gist/read:org/repo only. Did not work around
  it; kept going locally and left it for the user.

### Stage 1
- Commit aad2b4a: `.[verification]` -> `.[verification,cockpit]`. Verified locally in a
  clean venv: fails without Pillow, passes with it.
- Tried building Qt locally to design the Qt job: system `qmake` points at a missing
  `/usr/lib/qt5/bin/qmake` (no system Qt dev). Used the separately extracted prefix
  documented in docs/QT_CLUSTER.md (`~/Downloads/volvo-qt-runtime/root/usr`) and a
  fresh clone of the pinned upstream. Both apps built; Qt tests: 29 passed under xvfb-run.
- Ruff baseline measured (see findings). Chose a blocking set that is clean today
  instead of mass-editing existing code.
- Qt job apt package list derived from the upstream QML imports. Could not be tested
  locally (different Qt install), so the job started as `continue-on-error: true`.

### Stage 2
- tools/ci/make_release.py built V3 with a throwaway key; `sha256sum -c SHA256SUMS` OK.
- Shell blocks of both workflows extracted and checked with `bash -n` (23 blocks, 0
  errors; no actionlint available). Executed the "Choose signing key" step both ways:
  dispatch without secret -> dry-run key written to GITHUB_ENV; tag push without
  secret -> exit 1 with the error message.

### Stage 3
- First trace_matrix run missed all `OTA-CAN-002-*` rows: ID regex allowed only
  uppercase after the first dash (`compatible` is lowercase). Fixed.
- Rows written as "동일 시험" / "mixed 파라미터" showed `no-function`. Made them inherit
  the row above's function. CAN-003-06 has no such hint and stays `no-function`.
- New tests caught a real bug: `discover()` called `relative_to(ROOT)` and crashed for a
  `--tests` dir outside the repo. Fixed with an `is_relative_to` check. 11/11 pass.

### Local verification before push
- CI-like (no Qt build, Pillow installed): 256 passed, 45 skipped; skip guard flagged the
  27 Qt skips and surfaced the 8 vcan skips.
- With Qt built: 283 passed, 18 skipped (10 hardware + 8 vcan), guard passes, ruff clean.

### Push and real-runner results
- User ran the device-flow approval (`gh auth refresh -s workflow`); scope now includes
  `workflow`. Pushed the branch.
- Run 43869114523 (e76cc33): lint, pytest 3.10, pytest 3.12, qt all success. Checked step
  level because `continue-on-error` can hide a failing job: every qt step succeeded;
  log shows `29 passed` and `skipped tests: 0`. The guessed apt list was right first try.
- Removed `continue-on-error` and the TODO from the qt job (95b42e3). While editing
  docs/CI_CD.md, a second `sed` in the same command had a broken expression and failed;
  the first one had already removed the sentence, so the doc was checked by hand.
- Run 43869556468 (95b42e3): all four jobs success with qt now blocking.
- `gh workflow run ivi-release --ref ci/cicd-pipeline` -> `could not find any workflows
  named ivi-release`: a workflow_dispatch workflow is only registered once it is on the
  default branch. Dry-run release must wait for the merge.

### Merge and release dry-run
- Opened PR #3 (body via the `pr` skill). Told the user creating a PR does not merge it;
  user then asked to merge too. Waited for checks first: both the push run (43870398845)
  and the PR run (43870530517) passed all 4 jobs; `MERGEABLE CLEAN`.
- Merged with a merge commit (keeps the 6 commits visible): 2f08eef. Kept the branch
  because the local worktree uses it.
- main CI after merge: run 43870904410, all 4 jobs success.
- Dispatched `ivi-release` on main with version 3.0.0 (now registered because the file is
  on the default branch): run 43870955138, test + build success. "Tag must point at main"
  and "Publish GitHub Release" steps were skipped as designed for workflow_dispatch;
  `gh release list` shows 0 releases. Downloaded artifact `ivi-3.0.0-dry-run`:
  `sha256sum -c SHA256SUMS` OK; build-info.json has commit 2f08eef and the run ID.

### Real signing key and public-key pinning
- User approved making this the project's official release key (boards still trust the
  bench throwaway key; installing these releases on a board needs the board's trusted
  key replaced).
- Generated Ed25519 key in the session scratchpad with umask 077, stored it with
  `gh secret set OTA_SIGNING_KEY --env release-signing` (environment already existed,
  auto-created by the dry-run), then `shred -u` the local file. Public key committed as
  release/release-public.pem (sha256 d779264b...cb49).
- Added `--expect-public-key`: production releases fail before building anything if the
  secret's key differs from the committed public key (guards against pasting the wrong
  secret). Dry-run (throwaway key) does not pin. 2 new tests -> 13/13.

- PR #4 first CI run: pytest 3.10/3.12 failed with `FileNotFoundError: .../release/release-public.pem`
  (lint and qt passed). Locally it passed because the file existed on disk. Root cause:
  `.gitignore:12: *.pem` silently kept the public key out of the commit. Kept the `*.pem`
  rule (it protects private keys) and added a single `!release/release-public.pem`
  exception. Did not merge until green. Had this gone unnoticed, the first tagged
  production release would have failed at `--expect-public-key`.

### Official release ivi-v3.0.0
- PR #4 merged after the fix: b73ac6e. Tagged `ivi-v3.0.0` (annotated) on b73ac6e and
  pushed. Release run 43872712225: test + build success, including "Tag must point at a
  commit that is on main" and "Publish GitHub Release".
- Verified independently: downloaded the Release, `sha256sum -c` OK, attached
  release-public.pem is byte-identical to the repo's, signature verifies with the repo
  key via capstone_ota.bundle.verify, build-info commit = b73ac6e.
  https://github.com/jsy202/CAPSTONE_OTA/releases/tag/ivi-v3.0.0

### vcan on the hosted runner
- The 8 skipped tests (tests/test_can_verdict.py, tests/test_cluster_can_verdict.py) only
  need a bindable `vcan0` via Python AF_CAN; no can-utils.
- Added a pytest-job step: `modprobe vcan`, falling back to installing
  `linux-modules-extra-$(uname -r)`; then `ip link add vcan0 type vcan` + up. Added a
  blocking skip guard `--forbid 'vcan0'`. Tried on the branch first, not on main.
- First run: `modprobe: FATAL: Module vcan not found in directory /lib/modules/6.8.0-1070-azure`
  -> fallback installed the extra modules (~55 s), vcan0 came up. pytest 3.12:
  265 passed, 38 skipped (was 256 / 46). Remaining skips: 27 Qt (covered by the qt job)
  + 10 hardware + 1 `qmlscene is needed for the actual animation queue check`. Job time grew ~100 s -> ~170 s.

### qmlscene checks
- The 1 extra skip was tests/test_cockpit_transitions.py (needs `qmlscene`). Also found
  tests/test_local_cockpit_replay.py uses `if shutil.which('qmlscene'):` and silently
  drops its QML load check with no skip recorded at all.
- Qt job: install `qmlscene`, run both files there, `command -v qmlscene` as a hard check,
  guard forbids `qmlscene` skips. Run 43874005183: qt 32 passed (was 29), 0 skipped.
- Net: across the pytest + qt jobs the only skipped tests left are the 10 hardware ones.

### Errors
| Error | Resolution |
|-------|------------|
| `ModuleNotFoundError: No module named 'PIL'` on main | install `cockpit` extra in CI |
| `--log-failed` empty | read job log via `gh api .../jobs/<id>/logs` |
| `Author identity unknown` | repo-local identity matching existing commits |
| push rejected: missing `workflow` scope | user approved `gh auth refresh -s workflow` |
| local `qmake` missing system Qt | extracted Qt prefix from docs/QT_CLUSTER.md |
| trace_matrix skipped lowercase TC IDs | widened regex |
| trace_matrix crash on out-of-repo `--tests` | `is_relative_to` check, covered by test |
| broken `sed` while editing docs | first edit had applied; verified by reading the file |
| `ivi-release` not dispatchable from branch | needs merge to default branch first |
| public key missing in CI (`*.pem` gitignored) | `!release/release-public.pem` exception |
| `Module vcan not found` on hosted runner | install `linux-modules-extra-$(uname -r)` then modprobe |

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| cockpit replay without Pillow (local) | fail | fail | reproduced |
| cockpit replay with Pillow (local) | pass | pass | ok |
| CI tool tests | 11 pass | 11 pass | ok |
| full suite, Qt built (local) | no Qt skips | 283 passed, 18 skipped | ok |
| Actions run 43869114523 | all green, 0 Qt skips | all green, 29 passed / 0 skipped | ok |
| Actions run 43869556468 (qt blocking) | all green | all green | ok |
| `ivi-release` dry-run (run 43870955138) | artifact, no Release | test+build success, Release step skipped, 0 releases, `sha256sum -c` OK | ok |
| main after merge (run 43870904410) | all green | all green | ok |
