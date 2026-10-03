# Verification Summary (20261003T094743Z)

**VERIFICATION RESULT: PASS**

| Gate | Status | Detail |
|---|---|---|
| G1 Unit verification (SWE.4) | PASS | 4 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G2 Status propagation / component integration (SWE.5) | PASS | 5 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G3 Normal OTA commit state transition (SWE.6) | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G4 Fault-injection rollback (SWE.6) | PASS | 5 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G5 Recovery verification (SWE.6) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G6 Full regression + change impact | PASS | 766 tests, 0 failures, 0 errors, 0 skipped; protected OTA paths unchanged |
| G7 Privilege / security regression | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |

| Requirement | Status | Detail |
|---|---|---|
| VR-UI-001 Stable application shows STABLE and the stable SW version | PASS | 4 cases passed |
| VR-UI-002 Running trial slot shows OTA TRIAL and the trial SW version from app launch | PASS | 4 cases passed |
| VR-UI-003 After commit the display becomes STABLE with the new version without an application restart | PASS | 3 cases passed |
| VR-UI-004 After rollback the display returns to the previous stable version and briefly shows RESTORED | PASS | 5 cases passed |
| VR-UI-005 Corrupt, missing, partial or inconsistent metadata shows UNKNOWN and never crashes | PASS | 37 cases passed |
| VR-UI-005Q Qt AppStatus model and badge fail closed and propagate properties (Qt 5.15 build) | PASS | 1 cases passed |
| VR-UI-006 The privileged slot journal is never exposed to the Cluster application | PASS | 5 cases passed |
| VR-UI-007 A status-display failure never blocks the Cluster application or the OTA services | PASS | 6 cases passed |
| VR-UI-008 Badge keeps the original cluster usable on the desktop x86_64 capture: no upstream content under the plate (one reviewed decorative overlap), legible, never red, no input capture | PASS | 8 cases passed |
| VR-UI-009 Customization is reproducible on the pinned upstream and preserves provenance | PASS | 5 cases passed |
| VR-UI-009U The patch applies to a real pinned upstream import and refuses re-application | PASS | 1 cases passed |
| VR-OTA-001 Software versions before and after an update are identifiable on the vehicle | PASS | 4 cases passed |
| VR-OTA-002 The trial software version equals the signed vehicle bundle target and the bundle cannot be altered | PASS | 3 cases passed |
| VR-OTA-003 A runtime semantic incompatibility rolls back both ECUs (whole-vehicle) | PASS | 4 cases passed |
| VR-OTA-004 ROLLED_BACK is reported only after recovery verification passes; failed recovery is never a success | PASS | 5 cases passed |
| VR-OTA-005 Power interruption during verification recovers the previous version set after boot | PASS | 14 cases passed |
| VR-OTA-006 Tampered artifacts and invalid signatures are rejected before any slot changes | PASS | 10 cases passed |
| VR-OTA-007 Declared configuration incompatibility (CAN protocol major, dependencies, target) is rejected before activation | PASS | 16 cases passed |
| VR-OTA-008 Cluster heartbeat loss after activation causes whole-vehicle rollback | PASS | 16 cases passed |
| VR-OTA-009 Update results are recorded durably and repeated commands return the recorded result | PASS | 3 cases passed |
| VR-OTA-010 Downgrade to an equal or older release is rejected (anti-rollback) | PASS | 3 cases passed |
| VR-SEC-001 Existing least-privilege deployment assets remain intact | PASS | 3 cases passed |
| VR-HW-001 On the two-Pi rig the Cluster display shows STABLE 1.0.0 -> OTA TRIAL 1.1.1 -> RESTORED/STABLE 1.0.0 | PENDING_HARDWARE | requires the two-Pi rig |
| VR-HW-002 On target hardware speed/RPM/gear/warning indicators behave exactly as before the patch | PENDING_HARDWARE | requires the two-Pi rig |
| VR-HW-003 The patched app builds for the Pi (Qt/ARM) and the Qt tests pass on target | PENDING_HARDWARE | requires the two-Pi rig |
