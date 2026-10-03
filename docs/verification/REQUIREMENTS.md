# Verification Requirements (generated)

Generated from `docs/verification/verification_measures.json` by `scripts/verify_cluster_ota.py --write-requirements`; a unit test fails if this file drifts.

Reference: Tailored from Automotive SPICE 4.0 (SWE.4-6, SUP.1/8/9), ISO 26262-6 verification structure and UN R156 software-update concepts. Not a compliance claim.

| ID | Requirement | Level | Gate | Kind |
|---|---|---|---|---|
| VR-UI-001 | Stable application shows STABLE and the stable SW version | SWE.4 unit | G1 | automated |
| VR-UI-002 | Running trial slot shows OTA TRIAL and the trial SW version from app launch | SWE.4 unit + SWE.5 integration | G1 | automated |
| VR-UI-003 | After commit the display becomes STABLE with the new version without an application restart | SWE.5 integration + SWE.6 software | G3 | automated |
| VR-UI-004 | After rollback the display returns to the previous stable version and briefly shows RESTORED | SWE.5 integration + SWE.6 software | G4 | automated |
| VR-UI-005 | Corrupt, missing, partial or inconsistent metadata shows UNKNOWN and never crashes | SWE.4 unit (fault injection) | G1 | automated |
| VR-UI-005Q | Qt AppStatus model and badge fail closed and propagate properties (Qt 5.15 build) | SWE.4 unit + SWE.5 component | G2 | conditional |
| VR-UI-006 | The privileged slot journal is never exposed to the Cluster application | SWE.6 software + configuration | G7 | automated |
| VR-UI-007 | A status-display failure never blocks the Cluster application or the OTA services | SWE.5 integration (fault injection) | G2 | automated |
| VR-UI-008 | The badge keeps the original cluster usable: no lamp or gauge covered, legible, never red, no input capture | SWE.5 component + desktop system check | G2 | automated |
| VR-UI-009 | Customization is reproducible on the pinned upstream and preserves provenance | SUP.8-inspired configuration check | G2 | automated |
| VR-UI-009U | The patch applies to a real pinned upstream import and refuses re-application | SUP.8-inspired configuration check | G2 | conditional |
| VR-OTA-001 | Software versions before and after an update are identifiable on the vehicle | SWE.4/5 (existing + new) | G1 | automated |
| VR-OTA-002 | The trial software version equals the signed vehicle bundle target and the bundle cannot be altered | SWE.6 software | G3 | automated |
| VR-OTA-003 | A runtime semantic incompatibility rolls back both ECUs (whole-vehicle) | SWE.6 software (fault injection) | G4 | automated |
| VR-OTA-004 | ROLLED_BACK is reported only after recovery verification passes; failed recovery is never a success | SWE.6 software | G5 | automated |
| VR-OTA-005 | Power interruption during verification recovers the previous version set after boot | SWE.6 software (fault injection) | G5 | automated |
| VR-OTA-006 | Tampered artifacts and invalid signatures are rejected before any slot changes | SWE.6 software (fault injection) | G4 | automated |
| VR-OTA-007 | Declared configuration incompatibility (CAN protocol major, dependencies, target) is rejected before activation | SWE.6 software (fault injection) | G4 | automated |
| VR-OTA-008 | Cluster heartbeat loss after activation causes whole-vehicle rollback | SWE.6 software (fault injection) | G4 | automated |
| VR-OTA-009 | Update results are recorded durably and repeated commands return the recorded result | SWE.5/6 | G3 | automated |
| VR-OTA-010 | Downgrade to an equal or older release is rejected (anti-rollback) | SWE.4/5 | G7 | automated |
| VR-SEC-001 | Existing least-privilege deployment assets remain intact | configuration regression | G7 | automated |
| VR-HW-001 | On the two-Pi rig the Cluster display shows STABLE 1.0.0 -> OTA TRIAL 1.1.1 -> RESTORED/STABLE 1.0.0 | hardware system test | HW | hardware |
| VR-HW-002 | On target hardware speed/RPM/gear/warning indicators behave exactly as before the patch | hardware regression | HW | hardware |
| VR-HW-003 | The patched app builds for the Pi (Qt/ARM) and the Qt tests pass on target | target build | HW | hardware |

## VR-UI-001 — Stable application shows STABLE and the stable SW version

- **Test case:** TC-UI-STABLE-001
- **Verification level:** SWE.4 unit (ISO 26262-6: Software unit verification)
- **Verification method:** automated unit test of decide()/read_snapshot()
- **Techniques:** requirements-based, positive, equivalence partitioning
- **Precondition:** slot journal phase=stable, selector on the stable slot
- **Expected result:** state=stable, version=stable_version, or initial version when the provisioned slot has none
- **Pass/Fail criterion:** PASS if every listed case passes; any other state/version is FAIL
- **Gate:** G1
- **UN R156 concept referenced:** R156: SW identification (RXSWIN-like version visibility)
- **Automated test / evidence:** `tests/unit/test_ui_status.py::test_provisioned_stable_uses_initial_version`; `tests/unit/test_ui_status.py::test_recorded_stable_version_wins_over_initial_version`; `tests/unit/test_ui_status.py::test_valid_installer_root_is_read`; `tests/unit/test_ui_status.py::test_run_once_writes_current_status`

## VR-UI-002 — Running trial slot shows OTA TRIAL and the trial SW version from app launch

- **Test case:** TC-UI-TRIAL-001
- **Verification level:** SWE.4 unit + SWE.5 integration (ISO 26262-6: Software unit verification; software integration verification)
- **Verification method:** automated unit tests and real A/B slot lifecycle
- **Techniques:** requirements-based, positive, boundary / state transition
- **Precondition:** trial slot selected (phase activating after selector switch, or trial)
- **Expected result:** state=trial, version=trial_version at the ExecStartPre snapshot of the restart
- **Pass/Fail criterion:** PASS if the trial is reported at the restart snapshot itself; reporting stable before the switch is required
- **Gate:** G1
- **UN R156 concept referenced:** R156: SW identification (RXSWIN-like version visibility)
- **Automated test / evidence:** `tests/unit/test_ui_status.py::test_selected_trial_slot_reports_trial_version`; `tests/unit/test_ui_status.py::test_activating_after_selector_switch_is_trial`; `tests/unit/test_ui_status.py::test_activating_before_selector_switch_is_still_stable`; `tests/integration/test_ui_status_lifecycle.py::test_trial_then_rollback_shows_1_0_0_1_1_1_1_0_0`

## VR-UI-003 — After commit the display becomes STABLE with the new version without an application restart

- **Test case:** TC-UI-COMMIT-001
- **Verification level:** SWE.5 integration + SWE.6 software (ISO 26262-6: Software integration and verification; testing of the embedded software (simulated))
- **Verification method:** real slot lifecycle + full zonal OTA path (TLS, CAN, coordinator)
- **Techniques:** requirements-based, state transition, interface
- **Precondition:** trial 1.1.1 active; vehicle verification passes; coordinator commits
- **Expected result:** display sequence stable 1.0.0 -> trial 1.1.1 -> stable 1.1.1; no restored mark; app restarted only once (activation)
- **Pass/Fail criterion:** PASS only for the exact sequence and COMMITTED coordinator phase
- **Gate:** G3
- **UN R156 concept referenced:** R156: update result recorded / visible
- **Automated test / evidence:** `tests/integration/test_ui_status_lifecycle.py::test_commit_reaches_stable_new_version_via_watch`; `tests/integration/test_ui_status_system.py::test_normal_update_display_sequence_stable_trial_stable_new`; `tests/integration/test_zonal_end_to_end.py::test_signed_bundle_commits_both_applications_together`

## VR-UI-004 — After rollback the display returns to the previous stable version and briefly shows RESTORED

- **Test case:** TC-UI-ROLLBACK-001
- **Verification level:** SWE.5 integration + SWE.6 software (ISO 26262-6: Software integration and verification)
- **Verification method:** real slot lifecycle + full zonal path with injected runtime defect; Qt boundary tests for the 15 s window
- **Techniques:** requirements-based, state transition, boundary, recovery
- **Precondition:** trial 1.1.1 on screen; whole-vehicle rollback restores A
- **Expected result:** display returns to stable 1.0.0 with restored_at set; RESTORED shown for <15 s, then STABLE; commit and staged abort never set it
- **Pass/Fail criterion:** PASS if the restored mark appears only for the visible trial->previous-version transition
- **Gate:** G4
- **UN R156 concept referenced:** R156: previous software version restoration
- **Automated test / evidence:** `tests/unit/test_ui_status.py::test_trial_to_stable_with_different_version_sets_restored_at`; `tests/unit/test_ui_status.py::test_restored_at_carries_over_while_stable_same_version`; `tests/unit/test_ui_status.py::test_commit_same_version_never_sets_restored_at`; `tests/integration/test_ui_status_lifecycle.py::test_staged_abort_never_shows_rollback`; `tests/integration/test_ui_status_system.py::test_runtime_defect_display_sequence_and_recovery_verification`

## VR-UI-005 — Corrupt, missing, partial or inconsistent metadata shows UNKNOWN and never crashes

- **Test case:** TC-UI-FAILSAFE-001
- **Verification level:** SWE.4 unit (fault injection) (ISO 26262-6: Software unit verification (fault injection))
- **Verification method:** automated fault-injection unit tests (helper); Qt unit tests (app model)
- **Techniques:** negative, fault injection, equivalence partitioning, boundary
- **Precondition:** status inputs: corrupt/partial/empty journal, missing journal or selector, foreign selector, journal/selector mismatch, invalid versions, interrupted output write
- **Expected result:** helper publishes unknown/null; previous status stays intact on an interrupted write; no exception escapes
- **Pass/Fail criterion:** PASS if every partition yields unknown (never a guessed version) and no crash
- **Gate:** G1
- **Automated test / evidence:** `tests/unit/test_ui_status.py::test_unreadable_state_is_unknown`; `tests/unit/test_ui_status.py::test_missing_selector_is_unknown`; `tests/unit/test_ui_status.py::test_stable_journal_with_foreign_selected_slot_is_unknown`; `tests/unit/test_ui_status.py::test_invalid_initial_version_fallback_is_unknown`; `tests/unit/test_ui_status.py::test_corrupt_or_partially_written_journal_reads_no_state`; `tests/unit/test_ui_status.py::test_foreign_selector_target_reads_no_slot`; `tests/unit/test_ui_status.py::test_missing_journal_reads_no_state`; `tests/unit/test_ui_status.py::test_missing_install_root_reads_nothing`; `tests/unit/test_ui_status.py::test_selector_to_missing_slot_directory_reads_no_slot`; `tests/unit/test_ui_status.py::test_absolute_selector_is_rejected_like_the_installer`; `tests/unit/test_ui_status.py::test_interrupted_write_keeps_previous_status_and_no_temporary`; `tests/unit/test_ui_status.py::test_read_previous_rejects_non_v1`; `tests/unit/test_ui_status.py::test_malformed_previous_restored_at_is_not_carried`

## VR-UI-005Q — Qt AppStatus model and badge fail closed and propagate properties (Qt 5.15 build)

- **Test case:** TC-UI-FAILSAFE-002
- **Verification level:** SWE.4 unit + SWE.5 component (ISO 26262-6: Software unit verification; software integration)
- **Verification method:** Qt Test (33 cases) and QML TestCase (14 cases) built with a real Qt 5.15.2 toolchain
- **Techniques:** negative, fault injection, boundary, interface
- **Precondition:** CAPSTONE_QT_DIR points at a Qt 5.15 prefix
- **Expected result:** all Qt unit and QML component cases pass
- **Pass/Fail criterion:** PASS if both Qt binaries report 0 failures; NOT_EXECUTED without a Qt toolchain
- **Gate:** G2
- **Automated test / evidence:** `tests/integration/test_dashboard_customization.py::test_qt_unit_and_component_tests_pass_when_qt_available`

## VR-UI-006 — The privileged slot journal is never exposed to the Cluster application

- **Test case:** TC-UI-SEC-001
- **Verification level:** SWE.6 software + configuration (ISO 26262-6: Software integration and verification)
- **Verification method:** system test over the full OTA path + deployment asset inspection
- **Techniques:** requirements-based, negative, interface
- **Precondition:** full rollback lifecycle; production unit files
- **Expected result:** state.json stays 0600; status file has only 4 display keys and mode 0644; app unit gains no ReadWritePaths; watcher runs as capstone-ota, not root
- **Pass/Fail criterion:** PASS if no OTA identifier/digest leaks and no privilege is widened
- **Gate:** G7
- **Automated test / evidence:** `tests/integration/test_ui_status_system.py::test_ui_status_never_exposes_privileged_journal`; `tests/integration/test_ui_status_assets.py::test_cluster_app_snapshots_status_without_blocking_or_widening_access`; `tests/integration/test_ui_status_assets.py::test_watch_service_is_unprivileged_and_hardened`; `tests/unit/test_ui_status.py::test_read_snapshot_never_writes`

## VR-UI-007 — A status-display failure never blocks the Cluster application or the OTA services

- **Test case:** TC-UI-ISOL-001
- **Verification level:** SWE.5 integration (fault injection) (ISO 26262-6: Software integration and verification)
- **Verification method:** automated tests of snapshot/watch failure handling + unit file contract
- **Techniques:** negative, fault injection, interface
- **Precondition:** missing output directory, unwritable output, corrupt journal during watch
- **Expected result:** snapshot exits 0; watch keeps polling; ExecStartPre uses '-' so the app still starts
- **Pass/Fail criterion:** PASS if no failure mode propagates to the application start or the watch loop
- **Gate:** G2
- **Automated test / evidence:** `tests/unit/test_ui_status.py::test_main_snapshot_returns_zero_when_output_directory_missing`; `tests/unit/test_ui_status.py::test_watch_survives_corrupt_state_and_recovers`; `tests/unit/test_ui_status.py::test_watch_continues_when_output_write_fails`; `tests/unit/test_ui_status.py::test_write_status_does_not_create_missing_directory`; `tests/integration/test_ui_status_assets.py::test_tmpfiles_owns_runtime_directory`; `tests/integration/test_ui_status_assets.py::test_status_initial_version_matches_zone_agent`

## VR-UI-008 — The badge keeps the original cluster usable: no lamp or gauge covered, legible, never red, no input capture

- **Test case:** TC-UI-VIS-001
- **Verification level:** SWE.5 component + desktop system check (ISO 26262-6: Testing of the embedded software (desktop, not target))
- **Verification method:** static checks, clearance-measurement unit tests and the desktop capture of the real patched app in 11 layouts
- **Techniques:** requirements-based, interface, boundary, review
- **Precondition:** patched upstream built with Qt 5.15.2 on x86_64, run under Xvfb
- **Expected result:** badge plate x>=1150 with >=2 px clearance in every layout (one reviewed decorative overlap); state sequence visible
- **Pass/Fail criterion:** PASS if static checks pass and the committed capture evidence reports failed=false
- **Gate:** G2
- **Automated test / evidence:** `tests/integration/test_dashboard_customization.py::test_badge_uses_spec_colors_never_red_and_never_takes_input`; `tests/integration/test_dashboard_customization.py::test_badge_and_model_hard_code_no_version_or_state_value`; `tests/integration/test_dashboard_customization.py::test_model_pins_source_path_limits_and_timing`; `tests/integration/test_dashboard_customization.py::test_clearance_passes_when_last_lamp_ends_left_of_plate`; `tests/integration/test_dashboard_customization.py::test_clearance_fails_when_upstream_content_touches_plate`; `tests/integration/test_dashboard_customization.py::test_reviewed_decorative_overlaps_are_explicit_and_few`; evidence `docs/verification/evidence/desktop-qt/capture.json` expects {"failed": false}

## VR-UI-009 — Customization is reproducible on the pinned upstream and preserves provenance

- **Test case:** TC-UI-CM-001
- **Verification level:** SUP.8-inspired configuration check
- **Verification method:** patch/apply-script tests; opt-in application to a real pinned import
- **Techniques:** requirements-based, negative, interface
- **Precondition:** CAPSTONE_UPSTREAM_DIR for the opt-in case
- **Expected result:** add-only patch touching exactly 4 files; apply refuses non-imports and re-application
- **Pass/Fail criterion:** PASS if static cases pass; opt-in case NOT_EXECUTED without an import
- **Gate:** G2
- **Automated test / evidence:** `tests/integration/test_dashboard_customization.py::test_patch_touches_only_the_four_allowed_upstream_files`; `tests/integration/test_dashboard_customization.py::test_patch_wires_model_badge_resource_and_sources`; `tests/integration/test_dashboard_customization.py::test_apply_script_is_strict_and_pinned`; `tests/integration/test_dashboard_customization.py::test_apply_refuses_a_tree_that_is_not_an_import`; `tests/integration/test_dashboard_customization.py::test_overlay_and_qt_test_files_exist`

## VR-UI-009U — The patch applies to a real pinned upstream import and refuses re-application

- **Test case:** TC-UI-CM-002
- **Verification level:** SUP.8-inspired configuration check
- **Verification method:** opt-in test against a real import of commit 79345291
- **Techniques:** positive, negative
- **Precondition:** CAPSTONE_UPSTREAM_DIR points at an import
- **Expected result:** apply exits 0 with marker and overlay files; second apply exits non-zero 'already applied'
- **Pass/Fail criterion:** PASS if both apply outcomes match; NOT_EXECUTED without an import
- **Gate:** G2
- **Automated test / evidence:** `tests/integration/test_dashboard_customization.py::test_patch_applies_to_pinned_upstream_and_refuses_reapplication`

## VR-OTA-001 — Software versions before and after an update are identifiable on the vehicle

- **Test case:** TC-OTA-ID-001
- **Verification level:** SWE.4/5 (existing + new)
- **Verification method:** existing heartbeat tests + UI status tests
- **Techniques:** requirements-based, state transition
- **Precondition:** provisioned, trial and committed states
- **Expected result:** heartbeat and UI report the running version in each state; provisioned version protected from downgrade
- **Pass/Fail criterion:** PASS if every listed case passes
- **Gate:** G1
- **UN R156 concept referenced:** R156: SW identification (RXSWIN-like version visibility)
- **Automated test / evidence:** `tests/unit/test_zonal_agent.py::test_heartbeat_tracks_trial_and_commit_numeric_version_and_rollover`; `tests/unit/test_zonal_agent.py::test_initial_application_version_protects_provisioned_slot_from_downgrade`; `tests/unit/test_zonal_agent.py::test_interrupted_activation_metadata_cannot_advertise_stale_stable_version`; `tests/unit/test_ui_status.py::test_provisioned_stable_uses_initial_version`

## VR-OTA-002 — The trial software version equals the signed vehicle bundle target and the bundle cannot be altered

- **Test case:** TC-OTA-BIND-001
- **Verification level:** SWE.6 software
- **Verification method:** system test comparing displayed trial version with the signed bundle; existing signature tests
- **Techniques:** requirements-based, negative, interface
- **Precondition:** signed bundle 2.0.0 with digital-cluster 1.1.1
- **Expected result:** displayed trial version == signed target; modified bundle rejected
- **Pass/Fail criterion:** PASS if versions match exactly and tampering is rejected
- **Gate:** G3
- **UN R156 concept referenced:** R156: authenticity and integrity of the update; R156: target configuration identification
- **Automated test / evidence:** `tests/integration/test_ui_status_system.py::test_trial_display_version_matches_signed_bundle_target`; `tests/unit/test_vehicle_bundle.py::test_changed_bundle_cannot_use_original_signature`; `tests/unit/test_signing.py::test_generic_document_signature_rejects_tampering_and_wrong_key`

## VR-OTA-003 — A runtime semantic incompatibility rolls back both ECUs (whole-vehicle)

- **Test case:** TC-OTA-FI-SEM-001
- **Verification level:** SWE.6 software (fault injection)
- **Verification method:** full zonal path with an injected Cluster speed-scale defect (speed_divisor=10)
- **Techniques:** fault injection, requirements-based, recovery
- **Precondition:** both trial apps active, protocol declarations compatible
- **Expected result:** FUNCTIONAL_VALUE_MISMATCH detected; Central and Cluster return to slot A; display 1.1.1 -> 1.0.0
- **Pass/Fail criterion:** PASS only if the defect is detected by functional verification and both ECUs are restored
- **Gate:** G4
- **UN R156 concept referenced:** R156: dependencies between updated systems verified; R156: failed update recovery
- **Automated test / evidence:** `tests/integration/test_zonal_end_to_end.py::test_runtime_semantic_defect_rolls_back_whole_bundle`; `tests/integration/test_ui_status_system.py::test_runtime_defect_display_sequence_and_recovery_verification`; `tests/unit/test_compatibility.py::test_configured_can_error_budget_never_masks_functional_failure`; `tests/unit/test_compatibility.py::test_no_functional_challenges_never_counts_as_verification`

## VR-OTA-004 — ROLLED_BACK is reported only after recovery verification passes; failed recovery is never a success

- **Test case:** TC-OTA-REC-001
- **Verification level:** SWE.6 software
- **Verification method:** existing coordinator/compatibility tests + system test evidence
- **Techniques:** requirements-based, negative, recovery
- **Precondition:** rollback executed
- **Expected result:** recovery verification (heartbeats + functional) passed before ROLLED_BACK; otherwise RECOVERY_FAILED
- **Pass/Fail criterion:** PASS if no path reports ROLLED_BACK without passing recovery verification
- **Gate:** G5
- **UN R156 concept referenced:** R156: previous software version restoration verified
- **Automated test / evidence:** `tests/unit/test_coordinator.py::test_failed_recovery_never_reports_successful_rollback`; `tests/unit/test_coordinator.py::test_remote_rollback_failure_is_recovery_failed_even_with_healthy_probe`; `tests/unit/test_compatibility.py::test_recovery_requires_stable_heartbeats`; `tests/unit/test_coordinator.py::test_cluster_rollback_reply_must_confirm_restored_stable_slot`; `tests/integration/test_ui_status_system.py::test_runtime_defect_display_sequence_and_recovery_verification`

## VR-OTA-005 — Power interruption during verification recovers the previous version set after boot

- **Test case:** TC-OTA-FI-PWR-001
- **Verification level:** SWE.6 software (fault injection)
- **Verification method:** existing and new power-loss tests over the full zonal path
- **Techniques:** fault injection, recovery, state transition
- **Precondition:** power cut in VERIFYING with both trials active
- **Expected result:** boot recovery -> ROLLED_BACK (UPDATE_INTERRUPTED), recovery verification passed, display stable 1.0.0
- **Pass/Fail criterion:** PASS if both ECUs return to A and recovery verification passed
- **Gate:** G5
- **UN R156 concept referenced:** R156: interrupted update recovery
- **Automated test / evidence:** `tests/integration/test_zonal_recovery.py::test_restart_during_verification_restores_both_stable_slots`; `tests/integration/test_ui_status_system.py::test_power_loss_during_verification_display_sequence`; `tests/unit/test_coordinator.py::test_restart_rolls_back_whole_bundle_and_resumes_idempotently`; `tests/unit/test_slots.py::test_startup_recovery_aborts_every_uncommitted_phase`

## VR-OTA-006 — Tampered artifacts and invalid signatures are rejected before any slot changes

- **Test case:** TC-OTA-FI-INT-001
- **Verification level:** SWE.6 software (fault injection)
- **Verification method:** existing tests: tampered archive, equal-length hash mismatch, forged signature
- **Techniques:** fault injection, negative, requirements-based
- **Precondition:** artifact or signature modified after signing
- **Expected result:** ABORTED / SIGNATURE_INVALID; no slot or journal change
- **Pass/Fail criterion:** PASS if every manipulation is rejected before staging
- **Gate:** G4
- **UN R156 concept referenced:** R156: authenticity and integrity of the update
- **Automated test / evidence:** `tests/integration/test_zonal_end_to_end.py::test_tampered_archive_aborts_before_either_slot_changes`; `tests/integration/test_zonal_end_to_end.py::test_unsigned_command_is_rejected_without_journal_change`; `tests/unit/test_zonal_agent.py::test_archive_hash_mismatch_of_equal_length_cannot_stage`; `tests/unit/test_signing.py::test_modified_signature_and_wrong_key_are_rejected`; `tests/unit/test_updater.py::test_modified_artifact_is_rejected_before_staging`; `tests/unit/test_archive.py::test_unsafe_archive_member_is_rejected_before_extraction`

## VR-OTA-007 — Declared configuration incompatibility (CAN protocol major, dependencies, target) is rejected before activation

- **Test case:** TC-OTA-FI-CAN-001
- **Verification level:** SWE.6 software (fault injection)
- **Verification method:** existing static compatibility tests
- **Techniques:** fault injection, negative, equivalence partitioning
- **Precondition:** bundle declares protocol_major 2 or unsatisfied dependency or wrong target
- **Expected result:** ABORTED before activation; slots unchanged
- **Pass/Fail criterion:** PASS if no incompatible bundle reaches activation
- **Gate:** G4
- **UN R156 concept referenced:** R156: compatibility with target HW/SW configuration
- **Automated test / evidence:** `tests/integration/test_zonal_end_to_end.py::test_declared_can_major_mismatch_is_rejected_before_activation`; `tests/unit/test_compatibility.py::test_declared_pair_major_mismatch_rejected_even_without_dependency_rules`; `tests/unit/test_vehicle_bundle.py::test_unsatisfied_dependency_has_stable_error_code`; `tests/unit/test_manifest.py::test_policy_rejects_wrong_target_expired_or_old_release`

## VR-OTA-008 — Cluster heartbeat loss after activation causes whole-vehicle rollback

- **Test case:** TC-OTA-FI-HB-001
- **Verification level:** SWE.6 software (fault injection)
- **Verification method:** existing heartbeat-loss scenario + CAN freshness unit tests
- **Techniques:** fault injection, recovery
- **Precondition:** Cluster stops trial heartbeats
- **Expected result:** ROLLED_BACK with HEARTBEAT_* or PROCESS_UNHEALTHY; Cluster on slot A
- **Pass/Fail criterion:** PASS if loss is detected and rolled back
- **Gate:** G4
- **UN R156 concept referenced:** R156: failed update recovery
- **Automated test / evidence:** `tests/integration/test_zonal_end_to_end.py::test_cluster_heartbeat_loss_after_activation_rolls_back`; `tests/unit/test_can_protocol.py::test_primary_counter_validation_rejects_gaps_and_accepts_single_step_rollover`; `tests/unit/test_zonal_agent.py::test_duplicate_frame_is_idempotent_but_stale_and_skipped_counter_reject`

## VR-OTA-009 — Update results are recorded durably and repeated commands return the recorded result

- **Test case:** TC-OTA-LOG-001
- **Verification level:** SWE.5/6
- **Verification method:** existing coordinator/slot idempotency tests
- **Techniques:** requirements-based, negative
- **Precondition:** completed transaction replayed
- **Expected result:** recorded outcome and evidence returned; no reinstall
- **Pass/Fail criterion:** PASS if replays never change state
- **Gate:** G3
- **UN R156 concept referenced:** R156: update process recorded
- **Automated test / evidence:** `tests/unit/test_coordinator.py::test_completed_execute_is_idempotent_and_rejects_changed_bundle`; `tests/unit/test_coordinator.py::test_old_completed_transaction_returns_recorded_failure_evidence`; `tests/unit/test_slots.py::test_commit_promotes_trial_and_delayed_duplicates_do_not_reinstall`

## VR-OTA-010 — Downgrade to an equal or older release is rejected (anti-rollback)

- **Test case:** TC-OTA-AR-001
- **Verification level:** SWE.4/5
- **Verification method:** existing manifest/updater/zone-agent tests
- **Techniques:** negative, boundary
- **Precondition:** offered version <= current
- **Expected result:** rejected before download/staging
- **Pass/Fail criterion:** PASS if every older/equal offer is rejected
- **Gate:** G7
- **UN R156 concept referenced:** R156: protection against unauthorized/unintended version change
- **Automated test / evidence:** `tests/unit/test_updater.py::test_current_or_older_release_is_rejected`; `tests/unit/test_zonal_agent.py::test_initial_application_version_protects_provisioned_slot_from_downgrade`; `tests/unit/test_updater.py::test_current_link_overrides_stale_state_for_anti_rollback`

## VR-SEC-001 — Existing least-privilege deployment assets remain intact

- **Test case:** TC-SEC-CFG-001
- **Verification level:** configuration regression
- **Verification method:** existing asset tests
- **Techniques:** regression, interface
- **Precondition:** repository unit/config files
- **Expected result:** fixed paths, separate accounts, mTLS broker, zonal units unchanged in contract
- **Pass/Fail criterion:** PASS if all existing asset tests pass
- **Gate:** G7
- **Automated test / evidence:** `tests/integration/test_config_assets.py::test_systemd_units_use_fixed_paths_and_least_privilege`; `tests/integration/test_config_assets.py::test_zonal_units_match_configs_and_network_assets`; `tests/integration/test_config_assets.py::test_mosquitto_requires_mutual_tls_and_acl`

## VR-HW-001 — On the two-Pi rig the Cluster display shows STABLE 1.0.0 -> OTA TRIAL 1.1.1 -> RESTORED/STABLE 1.0.0

- **Test case:** TC-HW-DEMO-001
- **Verification level:** hardware system test
- **Verification method:** manual demonstration with video, candump and journal evidence (docs/RPI_VALIDATION_CHECKLIST.md)
- **Techniques:** requirements-based, state transition
- **Precondition:** two Raspberry Pi 4B, MCP2515 CAN, Qt ARM build of the patched app
- **Expected result:** sequence visible on camera; journal and status file match
- **Pass/Fail criterion:** PASS only with recorded hardware evidence
- **Gate:** HW
- **Automated test / evidence:** none — manual hardware validation

## VR-HW-002 — On target hardware speed/RPM/gear/warning indicators behave exactly as before the patch

- **Test case:** TC-HW-REG-001
- **Verification level:** hardware regression
- **Verification method:** manual side-by-side check against the unpatched build
- **Techniques:** regression
- **Precondition:** target display 1280x480 rotated 180 degrees
- **Expected result:** identical gauge behaviour; badge does not cover lamps
- **Pass/Fail criterion:** PASS only with recorded hardware evidence
- **Gate:** HW
- **Automated test / evidence:** none — manual hardware validation

## VR-HW-003 — The patched app builds for the Pi (Qt/ARM) and the Qt tests pass on target

- **Test case:** TC-HW-BUILD-001
- **Verification level:** target build
- **Verification method:** cross/native build on the Pi
- **Techniques:** regression
- **Precondition:** Pi OS with Qt 5.15
- **Expected result:** build succeeds; run-qt-tests.sh passes on target
- **Pass/Fail criterion:** PASS only with recorded target build log
- **Gate:** HW
- **Automated test / evidence:** none — manual hardware validation
