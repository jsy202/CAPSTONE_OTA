# Task Plan: CI/CD pipeline and test automation (portfolio scope)

## Goal
Make the existing GitHub Actions CI trustworthy (green means what it says), add a CD
step that fits an OTA project (build + sign + self-verify an IVI release, not a server
deploy), and link the test-case spec to automated results. Purpose stated by the user:
resume / portfolio. Physical-board (Pi1/Pi2/Pi3) automation is explicitly OUT of scope:
no always-on lab machine for a self-hosted runner, and the public repo must not expose
boards or signing keys to a runner.

## Scope decisions (user)
- Stages 1-3 only. Stage 4 (self-hosted runner + HIL on real boards) dropped.
- Work on branch `ci/cicd-pipeline`, never push to main directly, never push a real
  `ivi-v*` tag (that would publish a GitHub Release).

## Phases
| # | Phase | Status |
|---|-------|--------|
| 1 | Root-cause the red main run (systematic-debugging) | complete |
| 2 | Stage 1: CI fix, ruff lint, Qt build+Xvfb job, skip guard | complete, green on Actions |
| 3 | Stage 2: release builder + `release.yml` | complete locally; workflow not yet run on Actions |
| 4 | Stage 3: TC traceability matrix | complete, runs in CI summary |
| 5 | Docs (`docs/CI_CD.md`, README link) | complete |
| 6 | Push + verify on real runner, make Qt job blocking | complete (runs 43869114523, 43869556468) |

## Next Step
1. Merge `ci/cicd-pipeline` into main (user decision).
2. After merge, run `ivi-release` manually (workflow_dispatch) once: dry-run with a
   throwaway key, artifacts only, no Release. It cannot be dispatched before the file
   exists on the default branch.
3. User generates the real Ed25519 key and stores it as `OTA_SIGNING_KEY` in the
   `release-signing` environment (steps in docs/CI_CD.md), adds required reviewers.
4. Optional: enable `vcan` on the hosted runner so 8 more tests stop skipping.
5. Optional: add TC rows for the two `SEC_005` hardware tests in docs/TEST_CASES.md.
