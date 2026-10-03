# Verification Plan — Cluster OTA Status Badge와 Zonal OTA

## 1. 범위

**검증 대상 변경**
- Digital Cluster 화면에 STABLE / OTA TRIAL / RESTORED 상태와 실행 중 SW 버전을 표시하는 기능입니다.
- 구성 요소는 `capstone_ota/agent/ui_status.py`, systemd/tmpfiles 자산, Qt `AppStatus`, `StatusBadge.qml`, upstream patch입니다.

**회귀 범위**
- 위 기능이 의존하는 기존 Zonal OTA 흐름: 서명 vehicle bundle, A/B slot, coordinator, CAN 호환성 검증, whole-vehicle rollback, recovery verification
- 저장소 전체 테스트

**범위 밖**
- OS·커널·부트로더 업데이트, 실제 차량, 안전 인증

## 2. 참고한 표준·프로세스 개념 (tailoring, 준수 주장 아님)

| 출처 | 참고한 개념 | 이 프로젝트에서의 대응 |
|---|---|---|
| Automotive SPICE 4.0 SWE.4 | Software Unit Verification | `decide()`/`read_snapshot()`/출력 함수, Qt `AppStatus::evaluate` 단위 검증 (Gate G1) |
| Automotive SPICE 4.0 SWE.5 | Component / Integration Verification | 실제 `ABSlotInstaller` 생애주기와 상태 파일, systemd 계약, QML property 전파 (Gate G2) |
| Automotive SPICE 4.0 SWE.6 | Software Verification: verification measure, pass/fail, entry/exit, 환경, 회귀 기준, 양방향 추적성, 결과 요약 | `verification_measures.json` → `verify_cluster_ota.py` → 생성된 Traceability Matrix와 Summary (Gate G3–G6) |
| Automotive SPICE SUP.1 | Quality Assurance | 판정 규칙 자동화(skip≠PASS, 없는 테스트=FAIL), 검증기 자체 검증(mutation), QA 관점 self-review |
| SUP.8 / SUP.9 / SUP.10 (최소) | 형상, 문제, 변경 관리 | upstream 고정 커밋 + add-only patch, 결함 ID(D-01~D-05)와 수정·재검증 기록, 설계 변경을 spec §8에 기록 |
| ISO 26262-6 | Software unit verification / software integration and verification / testing of the embedded software | 아래 3절 계층 매핑 |
| UN R156 | 업데이트 전후 SW 식별, 대상 HW/SW 호환성, 시스템 간 의존성, 무결성·진정성, 실패·중단 복구, 이전 버전 복원, 결과 기록 | VR-OTA-001~010 (요구사항 문서의 "UN R156 concept referenced") |

이 프로젝트는 ISO 26262, Automotive SPICE, UN R156을 **준수하거나 인증받은 구현이 아닙니다**.
위 개념을 캡스톤 규모에 맞게 참고해 검증 구조를 설계했습니다.

## 3. 검증 계층 (ISO 26262-6 구조 참고)

| 계층 | 대상 | 방법 | Gate |
|---|---|---|---|
| Unit | 상태 판정 함수, metadata parser, fallback, restored 판정, Qt 모델 | pytest 단위 테스트, Qt Test | G1, G2 |
| Component / Integration | status publisher ↔ 실제 A/B slot, systemd 계약, AppStatus ↔ QML | pytest 통합 테스트, QML TestCase, 정적 계약 검사 | G2 |
| Software / System integration | 실제 TLS 서버, 두 ECU의 실제 A/B slot, zone agent, coordinator, 가상 CAN, UI 상태 출처 | 상태 전이 시나리오 테스트, fault injection | G3, G4, G5 |
| Desktop system check | 패치된 실제 upstream 앱 (Qt 5.15.2 x86_64, Xvfb) | 11개 레이아웃 화면 캡처, 자동 간섭(clearance) 측정 | G2 (VR-UI-008) |
| Target hardware | Raspberry Pi 2대, MCP2515, Qt ARM | 수동 시연, 영상·candump·journal | HW (대기) |

## 4. 검증 기법

| 기법 | 적용 예 |
|---|---|
| Requirements-based testing | 요구사항 하나마다 Test Case ID와 자동 테스트를 연결 |
| Positive testing | stable/trial 정상 매핑, 정상 commit |
| Negative testing | 잘못된 schema, version, selector, 위조 서명 |
| Equivalence partitioning | state {stable, trial, invalid, missing}<br>version {유효 SemVer, 없음, 빈 값, 형식 오류, 33자}<br>metadata {정상, 없음, 부분 기록, 손상 JSON, 잘못된 slot, journal/selector 불일치} |
| Boundary / state transition | 활성화 직전/직후 selector, RESTORED 표시 15 s 경계(14.999 s / 15 s / 미래 시각), 상태 전이 3종 |
| Fault injection | 아래 6절 12개 시나리오 |
| Interface testing | 상태 파일 v1 schema, systemd 인자, IPC 비노출, patch 대상 파일 |
| Regression testing | 전체 suite + 보호 경로 무변경(change impact) |
| Recovery testing | rollback 후 recovery verification, 전원 차단 후 부팅 복구 |
| Mutation (테스트 민감도 확인) | trial 규칙 변경, restored 비활성화, 15 s→16 s, env 검증 제거 → 실패 검출 확인 |

## 5. 상태 전이 검증 (기대 상태와 버전)

| 시나리오 | 기대 표시 순서 | 추가 판정 |
|---|---|---|
| 정상 update | STABLE 1.0.0 → OTA TRIAL 1.1.1 → STABLE 1.1.1 | `COMMITTED`, restored 표시 없음, commit 시 앱 재시작 없음 |
| runtime defect | STABLE 1.0.0 → OTA TRIAL 1.1.1 → STABLE 1.0.0 (restored 표시) | `FUNCTIONAL_VALUE_MISMATCH`, 검증 scope [trial, recovery] = [실패, 통과], 두 ECU slot A |
| power interruption | STABLE → OTA TRIAL (VERIFYING) → 전원 차단 → 부팅 → STABLE 1.0.0 | `ROLLED_BACK` (`UPDATE_INTERRUPTED`), recovery 통과, 재부팅 후 RESTORED 표시 없음(알려진 제한) |

## 6. Fault Injection 카탈로그

| # | 결함 | 주입 방법 | 기대 결과 | 자동 테스트 |
|---|---|---|---|---|
| 1 | runtime metadata 손상 | `state.json` 손상·부분 JSON, 상태 파일 손상 | unknown, crash 없음 | `test_corrupt_or_partially_written_journal_reads_no_state`, Qt `invalidInputFailsClosedToUnknown`, 캡처 `5-corrupt` |
| 2 | metadata 없음 | journal, selector, 설치 루트, 상태 파일 삭제 | unknown, 또는 env fallback 후 unknown | `test_missing_journal_reads_no_state`, `test_missing_install_root_reads_nothing`, Qt `missingFileUsesEnvironmentThenUnknown` |
| 3 | active-slot/journal 불일치 | stable journal + B 선택, 외부·절대 경로 selector | unknown (추측 금지) | `test_stable_journal_with_foreign_selected_slot_is_unknown`, `test_foreign_selector_target_reads_no_slot` |
| 4 | 잘못된 SW version | 빈 값, 개행, 65자, HTML, 33자 | unknown | `test_invalid_initial_version_fallback_is_unknown`, Qt invalid version rows |
| 5 | stale metadata | 오래된 restored 표시, 미래 시각, 잘못된 restored_at 타입 | 15 s 후 STABLE, carry 거부 | Qt `restoredWindowBoundaries`, `test_malformed_previous_restored_at_is_not_carried` |
| 6 | 상태 파일 기록 중단 | rename 직전 실패 주입 | 이전 상태 유지, temp 파일 없음 | `test_interrupted_write_keeps_previous_status_and_no_temporary` |
| 7 | Cluster heartbeat 손실 | trial heartbeat 차단 | whole-vehicle rollback | `test_cluster_heartbeat_loss_after_activation_rolls_back` (기존) |
| 8 | runtime semantic mismatch | Cluster 속도 1/10 표시 (`speed_divisor=10`) | `FUNCTIONAL_VALUE_MISMATCH` → 두 ECU rollback | `test_runtime_semantic_defect_rolls_back_whole_bundle` (기존), `test_runtime_defect_display_sequence_and_recovery_verification` |
| 9 | artifact hash 불일치 | 서명 후 아카이브 1바이트 변경, 같은 길이 hash 불일치 | 활성화 전 `ABORTED` | `test_tampered_archive_aborts_before_either_slot_changes`, `test_archive_hash_mismatch_of_equal_length_cannot_stage` (기존) |
| 10 | 잘못된 서명 | 서명을 0으로 덮어씀 | `SIGNATURE_INVALID`, journal 무변경 | `test_unsigned_command_is_rejected_without_journal_change` (기존) |
| 11 | CAN protocol major 불일치 | `protocol_major=2`로 재서명 | 활성화 전 거부 | `test_declared_can_major_mismatch_is_rejected_before_activation` (기존) |
| 12 | 검증 중 전원 차단 | VERIFYING에서 `PowerLoss` (BaseException) | 부팅 복구 → `ROLLED_BACK`, recovery 통과 | `test_restart_during_verification_restores_both_stable_slots` (기존), `test_power_loss_during_verification_display_sequence` |

기존 저장소의 7~12번 테스트는 다시 구현하지 않았습니다. Traceability Matrix에 공식 증적으로 연결했습니다.

## 7. Entry / Exit Criteria

**Entry**
- spec과 plan이 승인됨
- feature branch 기준선(dca36a8)에서 전체 테스트 651 passed
- 보호 경로 목록이 정의됨

**Exit (VERIFICATION RESULT: PASS)**
- Gate G1~G7이 모두 PASS
- 측정 항목 중 FAIL 0건
- 전체 회귀 failure/error 0건
- 보호 경로 무변경
- 자동 측정 항목에 NOT_EXECUTED가 0건 (Qt 툴체인과 upstream import가 있는 환경에서 실행)
- NOT_EXECUTED가 있으면 결과는 **INCOMPLETE**(exit 2)이고 PASS로 보고하지 않음
- PENDING_HARDWARE 항목은 보고서에 따로 명시함. 최종 판정 문구는 "PASS WITH HARDWARE VALIDATION PENDING"

## 8. Pass/Fail 판정 규칙 (자동화됨)

- 측정 항목은 연결된 모든 테스트 케이스(parameter 포함)가 통과해야 PASS입니다.
- Traceability에 적힌 테스트가 존재하지 않으면 **FAIL**입니다(추적성 깨짐).
- skip된 테스트는 **NOT_EXECUTED**입니다. PASS로 세지 않습니다.
- Gate는 FAIL이 0건이고 실행된 PASS가 1건 이상이며 NOT_EXECUTED가 0건이어야 PASS입니다.
  NOT_EXECUTED가 있으면 INCOMPLETE입니다.
- 전체 결과는 PASS(exit 0), INCOMPLETE(exit 2), FAIL(exit 1) 중 하나이고, FAIL이 다른 결과보다 우선합니다.
- G6는 전체 회귀 failure/error 0건과 보호 경로 무변경을 모두 만족해야 합니다.
- 하드웨어 항목은 PENDING_HARDWARE로 따로 표시합니다.

## 9. 검증 환경

| 항목 | 값 |
|---|---|
| OS | Ubuntu 22.04 x86_64 (Linux 6.8) |
| Python | 3.10 (CI: 3.10, 3.12) |
| Qt | 5.15.2 gcc_64, 사용자 로컬 설치(aqtinstall). Pi의 Qt/ARM과 다름 |
| 화면 | Xvfb 1280×800, 앱 창 1280×480. Pi의 180° 회전은 재현하지 않음 |
| CAN | 가상 CAN (`tests/integration/zonal_harness.py`). 하드웨어 SocketCAN 아님 |
| 실행 명령 | `CAPSTONE_QT_DIR=<qt> CAPSTONE_UPSTREAM_DIR=<import> scripts/verify-cluster-ota-demo.sh` |

## 10. 회귀 검증 기준

- 모든 변경 후 전체 suite를 실행하고 기준선 651개를 포함해 0 failure여야 합니다.
- 보호 경로는 merge-base 대비 무변경이어야 합니다. 대상은 coordinator, common, slots, zonal, publisher, zonal harness, 기존 zonal/slot/coordinator 테스트입니다.
- 결함을 수정하면 재현 테스트가 RED→GREEN이 되어야 하고, 이어서 전체 suite를 다시 실행합니다.

## 11. Verification Gates

| Gate | 내용 | 필수 |
|---|---|---|
| G1 | Unit verification PASS | 예 |
| G2 | Status propagation / component integration PASS | 예 |
| G3 | Normal OTA commit state transition PASS | 예 |
| G4 | Fault-injection rollback PASS | 예 |
| G5 | Recovery verification PASS | 예 |
| G6 | Full regression + change impact PASS | 예 |
| G7 | Privilege / security regression PASS | 예 |
| HW | Raspberry Pi 하드웨어 검증 | 대기 (PASS로 집계 안 함) |

## 12. Application Contract 검증 (Part B, stacked branch)

| Gate | 내용 | 주요 증적 |
|---|---|---|
| A1 | Central unit: heartbeat, counter, identity, profile, scheduler(fake clock) | `tests/unit/test_central_control.py` |
| A2 | Cluster IPC와 모델 통합 (Qt Test, 실제 바이너리) | `cluster_signals_test`, `test_cluster_app_ipc.py` |
| A3 | 프로토콜 적합성: 실제 `ApplicationIpc` 클라이언트, 코덱, wall-clock cadence, maintenance 유지 | `test_central_control_app.py` |
| A4 | 정상 호환성 검증 (실제 앱) | `test_application_contracts_system.py` |
| A5 | 의미 결함 검출 (실제 결함 빌드) | 같은 파일, `test_cluster_app_ipc.py` |
| A6 | Whole-vehicle rollback (실제 앱) | 같은 파일 |
| A7 | Recovery verification (실제 앱) | 같은 파일 |
| A8 | 보안·견고성: 소켓 권한, 특수 파일, 잘못된 IPC 상대, CAN 오류 | unit, integration, asset tests |
| A9 | 전체 회귀 + 변경 영향 (G6와 같은 규칙) | verifier |

**Timing 수용 기준:**
- unit 테스트는 fake clock으로 정확한 프레임 수를 확인합니다.
- wall-clock 테스트는 실제 루프를 3 s 동안 0.1 s/0.05 s 주기로 실행하고, 중앙값이 명목 주기 ±20 % 안이고 가장 긴 간격이 명목 주기의 2배 이하이면 통과입니다.
- 근거: validator의 `max_missed_heartbeats`와 `heartbeat_timeout_s`보다 충분히 엄격하고, CI 스케줄링 지연은 허용하는 수준입니다.

**추가 fault injection:**
- Central: heartbeat 중단(identity 손실), counter 넘김, 잘못된 identity 분할, 잘못된·중첩·부분·무응답 IPC, CAN 전송 오류, 재시작 시 maintenance 유지
- Cluster: 잘못된 envelope, 범위 밖 값, 모델 없음, 무응답·과대 입력 상대, 결함 빌드
- 기존 OTA fault 테스트는 다시 만들지 않고 추적성에 연결했습니다.
