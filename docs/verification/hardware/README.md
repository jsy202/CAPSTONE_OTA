# Hardware Verification Evidence (template)

The Raspberry Pi hardware validation (VR-HW-001~003) has **not been run yet**.
This folder holds only the format for recording results. Never fill in
PASS without running the test.

How to run: [CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md](../../CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md)

## Layout

```text
docs/verification/hardware/
  README.md                       # this file (format)
  <YYYY-MM-DD>-<scenario>/        # one folder per scenario/run
    record.md                     # the table below, filled in
    ui.json slot.json active-slot.txt journal.log can0.log vehicle.json mqtt.log
    photo-*.jpg / video link      # keep large videos outside the repo, link only
```

## record.md format

| Field | Value |
|---|---|
| Date (time zone) | |
| Commit SHA | |
| Tester | |
| Device (Central / Cluster asset ID) | |
| OS / kernel | |
| Architecture (`uname -m`) | |
| Qt version (`qmake -query QT_VERSION`) | |
| Scenario (B3 / Drill 1-3 / A / B / C) | |
| Requirement ID (VR-HW-001/002/003) | |
| Checklist item (B1-B8) | |
| Before slot / version | |
| Trial slot / version | |
| Injected fault | |
| Detected error (`last_error.code`) | |
| Rollback result (phase sequence) | |
| Recovery verification (pass/fail) | |
| Final slot / version | |
| Badge shown on screen (in order) | |
| Result (PASS / FAIL / N/A) | |
| Evidence files | |
| Notes (deviations, environment differences) | |

Verdict rule: PASS only when every expected result in the runbook matches. If
any one item differs, record FAIL and note the difference.
