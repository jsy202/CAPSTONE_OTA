# Verification Summary (20261003T125101Z)

**VERIFICATION RESULT: PASS**

| Gate | Status | Detail |
|---|---|---|
| G1 Unit verification (SWE.4) | PASS | 4 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G2 Status propagation / component integration (SWE.5) | PASS | 5 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G3 Normal OTA commit state transition (SWE.6) | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G4 Fault-injection rollback (SWE.6) | PASS | 5 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G5 Recovery verification (SWE.6) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| G6 Full regression + change impact | PASS | 856 tests, 0 failures, 0 errors, 0 skipped; protected OTA paths unchanged |
| G7 Privilege / security regression | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A1 Central unit verification | PASS | 4 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A2 Cluster IPC / model integration | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A3 Application protocol conformance | PASS | 3 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A4 Normal compatibility verification (real apps) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A5 Semantic fault detection (real fault build) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A6 Whole-vehicle rollback (real apps) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A7 Recovery verification (real apps) | PASS | 2 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A8 Application security / privilege regression | PASS | 5 PASS, 0 FAIL, 0 NOT_EXECUTED |
| A9 Full regression + change impact (application contracts) | PASS | 856 tests, 0 failures, 0 errors, 0 skipped; protected OTA paths unchanged |

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
| VR-APP-001 Central publishes a valid 0x100 heartbeat every heartbeat period (1 s default) | PASS | 4 cases passed |
| VR-APP-002 Central heartbeat version/state follow the active A/B application, never hard-coded | PASS | 11 cases passed |
| VR-APP-003 Central heartbeat and vehicle counters are contiguous modulo 256 and do not burst after a stall | PASS | 3 cases passed |
| VR-APP-004 Central publishes deterministic in-contract vehicle status on 0x200 at the vehicle period (100 ms default) | PASS | 11 cases passed |
| VR-APP-005 Central maintenance IPC conforms to ApplicationIpc v1 and keeps CAN traffic flowing | PASS | 10 cases passed |
| VR-APP-006 Cluster vehicle IPC updates the real dashboard models the QML scene renders | PASS | 2 cases passed |
| VR-APP-007 Cluster functional IPC returns the values the application actually interpreted | PASS | 3 cases passed |
| VR-APP-008 The functional response is never a blind echo of the request (speed, rpm and warnings via the models; gear is interpreted state without an upstream display) | PASS | 2 cases passed |
| VR-APP-015 The Central application ships as a reproducible slot payload | PASS | 2 cases passed |
| VR-APP-009 Malformed, oversized, nested, partial or silent IPC peers neither crash nor block the Central application | PASS | 16 cases passed |
| VR-APP-009Q Malformed, oversized, partial or silent IPC peers neither crash nor block the real Qt Cluster | PASS | 1 cases passed |
| VR-APP-010S The fault build exists only behind an explicit qmake variable, is labelled, and is refused as a normal payload | PASS | 3 cases passed |
| VR-APP-010 A DEMO/TEST fault build produces a real display/model mismatch that the validator reports as FUNCTIONAL_VALUE_MISMATCH | PASS | 2 cases passed |
| VR-APP-011C The real Central application passes trial verification and commits (harness Cluster) | PASS | 1 cases passed |
| VR-APP-011 The normal real application set (Central + Qt Cluster) passes trial verification and commits | PASS | 1 cases passed |
| VR-APP-012C Losing the real Central identity during trial rolls back the whole vehicle | PASS | 1 cases passed |
| VR-APP-012 A real Cluster fault build in trial rolls back the whole vehicle | PASS | 1 cases passed |
| VR-APP-013C Recovery verification confirms STABLE heartbeats from the real Central application after rollback | PASS | 2 cases passed |
| VR-APP-013 Recovery verification confirms the previous stable application set with the real Qt Cluster (rollback and power loss) | PASS | 2 cases passed |
| VR-APP-014 Central IPC socket and units do not weaken the existing privilege boundaries | PASS | 8 cases passed |
| VR-APP-014Q The real Qt Cluster IPC socket is owner/group only | PASS | 1 cases passed |
| VR-APP-016 Transient CAN send errors (ENOBUFS/ENETDOWN) never stop the Central application | PASS | 1 cases passed |
| VR-APP-017 Maintenance survives the Central restart performed by activate_trial | PASS | 3 cases passed |
