# Remove the obsolete display-regression scenario

User direction: CPU contention is the main scenario; retain recovery-slot substitution security tests.

1. Remove mixed/replayed-display entry points and dedicated failure tests. Keep normal CAN signal, counter, freshness and observer identity checks.
2. Decouple the V1 substitution fixture from display mismatches: verify the candidate normally, substitute only the recorded recovery slot during a V3 trial, and return an explicitly labeled test-only rollback request. This is not CPU anomaly evidence.
3. Keep physical bench baseline/cleanup working without the mixed scenario; update active docs and mark historical results as historical, without rewriting past evidence.
4. Run focused regression tests and the full local suite; no hardware runs. Commit and push main after verification.

Review focus: baseline must not be swapped; failed/missing observation must never be treated as a successful attack test; authentic V1 must remain blocked before execution; removed profile must fail explicitly; live CPU services are untouched.

User refinement: one fixed cluster, no cluster V1/V2/V3 variants. Remove its badge/config/policy branches. Move the local signed recovery attack bench to IVI (no CPU stress in the security fixture). Preserve low-level physical transport/security tests with one fixed cluster binary, but do not claim they validate the new IVI field scenario. Do not deploy to boards in this task.

## Verification (2026-11-10)

- Removed buffered profile: regression observed RED (old verifier attempted target access), then GREEN.
- IVI initialization tree immutability: RED due to __pycache__, GREEN with PYTHONDONTWRITEBYTECODE=1 in the payload launcher.
- IVI relay selection and verification deadline: RED with cluster-only dispatch/300s timeout, GREEN with explicit application and bounded IVI deadline.
- Fixed Qt cluster: rebuilt C++/QML successfully using the pinned Volvo source and local Qt prefix.
- Final `.venv/bin/python -m pytest -q`: **258 passed, 9 hardware tests skipped, 95.71s**. Includes real local two-hop IVI security trial, authentic V1 signature, journal unchanged, recovery refusal and no V1 execution.
- Independent review: corrected IVI relay timeout; no remaining important findings.
- `git diff --check`, updated document local links/code fences, and changed-file private key block scan passed.
- No board deployment or physical test run. New ARM build is required before physical use; historical evidence remains unchanged except explanatory banners.
