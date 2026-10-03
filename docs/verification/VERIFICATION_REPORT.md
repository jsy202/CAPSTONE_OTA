# Verification Report — Cluster OTA Status Badge와 Zonal OTA

| 항목 | 값 |
|---|---|
| 대상 | feature/cluster-status-badge (기준선 `dca36a8`) + feature/zonal-application-contracts (Part B, 기준 `ab76590`) |
| 실행일 | 2026-10-03 |
| 실행 명령 | `CAPSTONE_QT_DIR=<Qt 5.15.2> CAPSTONE_UPSTREAM_DIR=<pinned import> scripts/verify-cluster-ota-demo.sh --snapshot docs/verification/evidence/latest` |
| 자동 판정 | **VERIFICATION RESULT: PASS** (Gate G1–G7 모두 PASS, NOT_EXECUTED 0건) |
| 최종 결론 | **PASS WITH HARDWARE VALIDATION PENDING** |

증적은 다음 위치에 있습니다.
- 자동 생성: `evidence/latest/`의 `summary.md`, `results.json`, `TRACEABILITY_MATRIX.md`, `evidence/VR-*.json`
- 데스크톱 화면: `evidence/desktop-qt/`

## 1. 범위

- **검증 대상:** Cluster 화면 OTA 상태 배지(STABLE / OTA TRIAL / RESTORED + SW 버전)
- **회귀 범위:** 배지가 의존하는 Zonal OTA 흐름과 저장소 전체 테스트
- **변경하지 않음:** OTA 핵심 코드. coordinator, common, slots, zonal, publisher, 기존 zonal/slot/coordinator 테스트이며, merge-base 대비 diff 0줄입니다.

## 2. 참고한 표준·프로세스 개념

- Automotive SPICE 4.0 inspired: SWE.4 / SWE.5 / SWE.6, SUP.1, 최소 범위의 SUP.8/9/10
- ISO 26262-6 verification structure referenced: unit / integration / embedded software testing
- UN R156 software-update verification concepts referenced

**어느 표준에도 준수하거나 인증받았다고 주장하지 않습니다.** 대응 관계는 [VERIFICATION_PLAN.md](VERIFICATION_PLAN.md) 2절에 있습니다.

## 3. 시험 환경

| 항목 | 값 | 한계 |
|---|---|---|
| Host | Ubuntu 22.04 x86_64, Python 3.10.12 | Pi(aarch64) 아님 |
| Qt | 5.15.2 gcc_64 (사용자 로컬 aqtinstall), GL 헤더는 Ubuntu .deb를 로컬에 풀어 사용 | Pi의 Qt/ARM, EGLFS 아님 |
| 화면 | Xvfb, 앱 창 1280×480 | Pi의 180° 회전, 실제 패널, 카메라 판독 미재현 |
| OTA 경로 | 실제 TLS 서버 2개, 두 ECU의 실제 A/B slot, 실제 zone agent, coordinator, 가상 CAN | systemd는 실행 시점만 에뮬레이션, SocketCAN/MCP2515 아님 |

## 4. 요구사항과 검증 방법

- **요구사항:** 25건
  - 자동 20건
  - 조건부 2건: Qt 툴체인 또는 upstream import가 필요하고, 이번 실행에서는 둘 다 실행됨
  - 하드웨어 3건: 대기
- **상세:** [REQUIREMENTS.md](REQUIREMENTS.md)에 요구사항마다 ID, 설명, 검증 수준, 방법, 기법, 전제조건, 기대 결과, Pass/Fail 기준, 자동 테스트, 증적이 있습니다. `verification_measures.json`에서 생성되고, 내용이 어긋나면 테스트가 실패합니다.
- **적용 기법:** requirements-based, positive, negative, equivalence partitioning, boundary/state transition, fault injection, interface, regression, recovery, mutation(테스트 민감도 확인)

## 5. Traceability

- **연결 단위:** 요구사항 25건 ↔ Test Case 25건 ↔ 자동 테스트 노드 97개(기존 저장소 테스트 37개 포함) ↔ 결과
- **생성 방식:** [evidence/latest/TRACEABILITY_MATRIX.md](evidence/latest/TRACEABILITY_MATRIX.md)는 이번 실행의 JUnit 결과로 자동 생성됐습니다. 역방향(테스트 → 요구사항) 표도 포함합니다.
- **무결성 확인:** 매트릭스에 적힌 모든 테스트 노드가 실제 suite에 존재하는지 `test_every_measure_test_node_exists_in_the_suite`가 확인합니다. 없는 테스트는 검증기가 FAIL로 판정합니다.
- **커버리지:** 자동 요구사항 22건 모두 1개 이상의 실행된 테스트에 연결되어 PASS입니다. 하드웨어 3건은 테스트가 없는 PENDING입니다.

## 6. 결과

| Gate | 결과 | 내용 |
|---|---|---|
| G1 Unit verification (SWE.4) | PASS | 4 measures |
| G2 Status propagation / component integration (SWE.5) | PASS | 5 measures (Qt 38/38, QML 14/14, 실제 패치 적용 포함) |
| G3 Normal OTA commit state transition | PASS | 3 measures |
| G4 Fault-injection rollback | PASS | 5 measures |
| G5 Recovery verification | PASS | 2 measures |
| G6 Full regression + change impact | PASS | pytest 766 passed, 0 failed, 0 skipped; 보호 경로 무변경 |
| G7 Privilege / security regression | PASS | 3 measures |

**수량**
- **자동 테스트:** pytest 766개 (기준선 651 + 신규 115)
  - `test_ui_status.py` 63개
  - `test_ui_status_lifecycle.py` 3개
  - `test_ui_status_system.py` 5개
  - `test_ui_status_assets.py` 4개
  - `test_dashboard_customization.py` 16개
  - `test_verify_cluster_ota.py` 21개
  - `test_cluster_badge_drill.py` 3개 (하드웨어 drill 도구)
- **Qt 케이스:** Qt Test 38개, QML TestCase 14개
- **데스크톱 시스템 확인:** 패치된 실제 앱의 상태 5개 화면, 11개 레이아웃 × (패치본 + 원본 기준선) 캡처

### 6.1 상태 전이 (system test 증적 `evidence/latest/evidence/*.json`)

| 시나리오 | 관측된 표시 순서 | 최종 상태 |
|---|---|---|
| 정상 update (VR-UI-003) | stable 1.0.0 → trial 1.1.1 → stable 1.1.1 | `COMMITTED`, 두 ECU slot B (Central 1.1.0, Cluster 1.1.1) |
| runtime defect (VR-OTA-003) | stable 1.0.0 → trial 1.1.1 → stable 1.0.0 (restored 표시) | 주입한 결함 `speed_divisor=10` → `FUNCTIONAL_VALUE_MISMATCH` 검출, 검증 scope [trial, recovery] = [실패, 통과], 두 ECU slot A, `ROLLED_BACK` |
| power interruption (VR-OTA-005) | 전원 차단 전 trial 1.1.1, 부팅 후 stable 1.0.0 | `ROLLED_BACK` (`UPDATE_INTERRUPTED`), recovery 통과, 재부팅 후 RESTORED 표시 없음(알려진 제한) |
| signed binding (VR-OTA-002) | 표시된 trial 버전 1.1.1 = 서명 번들 target 1.1.1 | 일치 |

### 6.2 데스크톱 화면 증적 (`evidence/desktop-qt/`)

- **상태 화면:** `STABLE SW 1.0.0` → `OTA TRIAL SW 1.1.1` (cyan 테두리와 상하 4 px 선) → `RESTORED SW 1.0.0` (amber) → `STABLE SW 1.0.0` → 손상 파일에서 `SW STATUS —` 순서로 바뀌었고, 앱 crash는 없었습니다.
- **원본 기준선 비교:**
  - 10개 레이아웃에서 배지 영역 아래 upstream 콘텐츠 0 px, 상하 선 영역 0 px였습니다.
  - OriginalEarly240Layout 1개만 패널의 장식 테두리선 583 px가 배지 아래로 지나갑니다. 사람이 PNG로 확인했고 지시등·게이지·값은 가리지 않습니다. PASS가 아니라 **REVIEWED**로 기록했습니다.
- **증거 신선도:** `capture.json`에 `StatusBadge.qml`/`app_status.cpp`의 SHA-256을 기록했습니다. 소스가 바뀌면 검증기가 이 증적을 stale로 보고 FAIL 처리합니다.

## 7. Fault Injection 시나리오

12개 모두 자동화했습니다.
- **1~6 (신규, UI 계층):** metadata 손상, metadata 없음, slot 불일치, 잘못된 버전, stale metadata, 기록 중단
- **7~12 (기존, OTA 계층):** heartbeat 손실, runtime semantic mismatch, hash 불일치, 잘못된 서명, CAN major 불일치, 전원 차단. 기존 테스트를 다시 구현하지 않고 공식 증적으로 연결했습니다.

주입 방법, 기대 결과, 테스트 이름은 [VERIFICATION_PLAN.md](VERIFICATION_PLAN.md) 6절에 있습니다.

## 8. Change Impact와 Regression

| 구분 | 대상 |
|---|---|
| Modified (신규 또는 변경) | runtime metadata publisher(`ui_status.py`), systemd 통합(`digital-cluster.service` ExecStartPre 1줄, 신규 watch unit, tmpfiles), Qt AppStatus, QML overlay, customization patch(upstream 4개 파일에 줄 추가만), 검증 도구와 문서 |
| Must remain unchanged | coordinator semantics, vehicle bundle, A/B slot semantics, CAN protocol, 서명 검증, artifact 검증, compatibility validator, whole-vehicle rollback |
| 비영향 증명 | (1) G6 보호 경로 diff 0줄(merge-base `dca36a8`)<br>(2) 기준선 651개 포함 전체 766개 통과<br>(3) 기존 OTA 테스트 37개를 G3–G7 요구사항의 증적으로 재사용해 모두 통과 |

## 9. 개발 중 발견한 결함

| ID | 발견 방법 | 결함 | 조치와 재검증 |
|---|---|---|---|
| D-01 | 첫 컴파일 전 코드 검토 | `QSet`을 서로 다른 두 `keys()` 임시 객체의 begin/end로 생성(UB) | 한 번만 바인딩. Qt 실패 입력 케이스 통과 |
| D-02 | 패치된 실제 앱 데스크톱 캡처(11개 레이아웃) | 배지(약 180 px)가 마지막 경고등(SHIFT UP)을 가림. QML 단위 테스트는 모서리 좌표만 확인해서 놓침 | 124×50 plate를 빈 여백(x ≥ 1150)으로 옮기고 상하 accent 선 추가. 자동 clearance/coverage 측정 추가 |
| D-03 | 같은 캡처 | 넓은 Handel Gothic 글꼴 때문에 제목이 "OTA TRI…"로 잘림 | ●/↺ 기호 제거, HorizontalFit 16→11 px. QML 테스트 RED→GREEN |
| D-04 | 같은 캡처 | Handel Gothic의 "1"이 "I"처럼 보여 `SW 1.1.1`이 `SW I.I.I`로 읽힘 | 버전 줄에 upstream 내장 Arial Black 사용. QML 테스트 RED→GREEN |
| D-05 | 검증기 자체 검증 | change-impact 목록에 같은 파일이 두 번 나옴 | 중복 제거. 테스트 RED→GREEN |
| R-1 | 최종 리뷰(독립 reviewer) | 상태 파일 경로의 FIFO/symlink: root 헬퍼와 GUI 스레드가 블록되거나 링크를 따라감 | `O_NOFOLLOW\|O_NONBLOCK` + 일반 파일 확인(Python, C++), ExecStartPre는 `setpriv`로 root를 버림. FIFO 테스트가 실제로 hang(RED) → GREEN |
| R-2 | 최종 리뷰 | NOT_EXECUTED가 있어도 결과가 무조건 PASS로 표시됨 | INCOMPLETE(exit 2) 판정 추가. Qt 없는 실제 실행에서 INCOMPLETE 확인 |
| R-3 | 최종 리뷰 | VR-UI-008 간섭 측정이 배지 아래에 가려진 콘텐츠를 보지 못하고, 증적이 소스와 연결되지 않음 | 원본 기준선 캡처 coverage 측정, 소스 SHA-256 신선도 검사 |
| R-4 | 최종 리뷰(Minor에서 Important로 재평가) | 쓰기 실패 시 이전 TRIAL 상태 파일이 남음 | 실패 시 파일 제거 → UI가 unknown 표시. 테스트 RED→GREEN |
| R-5 | 최종 리뷰(Minor에서 Important로 재평가) | 매우 큰 `restored_at`을 ms로 변환할 때 C++ UB | 범위 [0, 1e12) 밖이면 unknown. Qt 테스트 RED→GREEN |

**테스트 민감도(mutation) 확인**
- Python trial 규칙 변경: system 테스트 2개 FAIL
- restored 비활성화: 2개 FAIL
- Qt 15 s→16 s와 env 검증 제거: 2개 FAIL
- 검증기:
  - 판정 변이(mutant): 요구사항 5건과 Gate 5개 FAIL, exit 1
  - 보호 파일 수정: G6 FAIL

모든 변이는 원복했고, 원복 후 전체 통과를 확인했습니다.

## 10. 잔여 위험과 한계

- **watch 서비스 중단:** `capstone-ota-ui-status`가 멈추면 앱을 재시작하지 않는 commit은 반영되지 않습니다. 상태 파일에 heartbeat가 없으므로 마지막 상태가 화면에 남습니다. `Restart=on-failure`로 완화했고, 리뷰 Minor로 보류했습니다.
- **RESTORED 표시 시간:** 15 s는 헬퍼가 rollback을 기록한 시각부터 계산합니다. Pi에서는 Qt 시작 시간만큼 실제 표시가 짧아질 수 있으므로 "최대 15 s"로 봐야 합니다.
- **재부팅 후 표시:** 재부팅으로 rollback된 경우에는 `/run`이 비워지므로 RESTORED가 표시되지 않습니다. 버전은 STABLE 1.0.0으로 올바르게 표시됩니다.
- **버전 길이:** 헬퍼는 64자까지 허용하고 UI는 32자까지입니다. Zonal 시스템의 CAN 버전은 uint8 세 자리라 실제로는 생기지 않습니다.
- **추가 hardening 보류:** watch unit의 PrivateNetwork, SystemCallFilter 등은 Minor로 보류했습니다.
- **에뮬레이션 범위:** system 테스트는 systemd 실행 시점을 에뮬레이션합니다. 실제 systemd에서 `-+`/`setpriv` 동작, tmpfiles 소유권, root가 남긴 파일을 watch가 다시 쓰는 동작은 하드웨어 체크리스트 B2에서 확인합니다.

- **watch 에뮬레이션 주기:** system 테스트는 coordinator 이벤트마다 watch를 한 번 실행합니다. 실제 서비스는 1 s 주기로 폴링하므로, 1 s보다 짧은 중간 상태는 화면에 나타나지 않을 수 있습니다. 최종 상태는 같습니다. 실제 타이밍은 B5에서 확인합니다.
- **소스 리터럴 검사:** 일부 정적 검사(`4096`, `15000`)는 구현 리터럴을 확인합니다. 해당 동작은 Qt 단위 테스트가 따로 검증하므로 보조 수단입니다.

## 10.1 QA 관점 self-review 결과

| 질문 | 판단 |
|---|---|
| 요구사항이 실제 테스트에 연결되는가 | 예. 노드 97개 존재를 자동 확인 |
| Pass/Fail 기준이 모호하지 않은가 | 요구사항마다 명시, Gate 판정 자동화 |
| 구현 세부만 확인하는 테스트가 있는가 | 리터럴 검사 일부가 그렇지만 동작 테스트와 함께 있어 보조 역할 |
| negative/fault case가 있는가 | 12개 fault injection, 동치 분할 |
| rollback 성공을 slot 변경만으로 판단하는가 | 아니오. recovery verification 결과와 최종 phase까지 확인 |
| 공유 mock 때문에 거짓 PASS가 날 위험 | 기대값을 harness가 아닌 서명 번들과 고정값에서 가져옴. watch 주기 차이는 위 잔여 위험으로 기록 |
| production 경로를 얼마나 통과하는가 | helper, slot, coordinator, zone agent, TLS 실제 코드. systemd만 에뮬레이션 |
| 보안 경계가 약해졌는가 | 아니오. 스냅샷은 root를 버리고(`setpriv`), no-follow/non-blocking 읽기, 앱 unit 권한 변화 없음 |
| 결과를 재현할 수 있는가 | 아래 12절 명령으로 재현 가능 |
| 실행하지 않은 검증을 PASS로 과장했는가 | 아니오. NOT_EXECUTED는 INCOMPLETE, 하드웨어는 PENDING |

## 11. 남은 하드웨어 검증 (PENDING HARDWARE VALIDATION)

| ID | 내용 | 절차 |
|---|---|---|
| VR-HW-001 | 두 Pi 실제 시연에서 STABLE 1.0.0 → OTA TRIAL 1.1.1 → RESTORED/STABLE 1.0.0 | `docs/RPI_VALIDATION_CHECKLIST.md` B3–B5, B7–B8 |
| VR-HW-002 | 패치 전후 속도·RPM·기어·경고등 동작 동일, 배지가 가리지 않음 | B6 |
| VR-HW-003 | Pi에서 패치된 앱 빌드, `run-qt-tests.sh` 통과 | B1–B2 |

실행 절차는 [CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md](../CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md)에 모았습니다.

**선행 조건 (갱신):** 두 Pi 전체 OTA 시나리오에 필요한 앱 계약(Central Control 앱, Qt `vehicle`/`functional` IPC)은 Part B에서 구현했고, 무하드웨어 E2E로 확인했습니다. 실물 실행은 아직 PENDING입니다.

Qt 검증 등급:
- **A (실행됨, x86_64 데스크톱):** Qt Test 38, QML 14, 패치된 upstream 전체 빌드(`-Werror`), Xvfb 실행·캡처
- **C (미실행):** 대상 하드웨어(ARM, EGLFS, 실제 패널)

## 12. 재현 방법

```bash
# 1) 자동 검증 (Qt와 upstream이 없으면 해당 항목은 NOT EXECUTED → INCOMPLETE)
scripts/verify-cluster-ota-demo.sh

# 2) 사용자 로컬 Qt 5.15.2 (시스템 변경 없음)
python3 -m pip install --target ./aqt aqtinstall
PYTHONPATH=./aqt python3 -m aqt install-qt linux desktop 5.15.2 gcc_64 -O ./Qt
export CAPSTONE_QT_DIR=$PWD/Qt/5.15.2/gcc_64

# 3) 고정 upstream import (패치 적용 실측용)
dashboard/volvo-digital-dash/import-upstream.sh /tmp/vddm/VolvoDigitalDashModels
export CAPSTONE_UPSTREAM_DIR=/tmp/vddm/VolvoDigitalDashModels
scripts/verify-cluster-ota-demo.sh --snapshot docs/verification/evidence/latest

# 4) 데스크톱 화면 증적
#    - 패치본과 원본을 각각 qmake && make
#    - GL 헤더가 없으면 apt-get download mesa-common-dev libgl-dev 후 dpkg -x,
#      QMAKE_INCDIR_OPENGL / QMAKE_LIBDIR_OPENGL 지정
#    - 원본 빌드는 Eigen ea2c020을 ../eigen에 체크아웃
python3 dashboard/volvo-digital-dash/customization/qt-tests/capture_dashboard_states.py \
  --app <patched>/VolvoDigitalDashModels --baseline-app <pristine>/VolvoDigitalDashModels \
  --qt "$CAPSTONE_QT_DIR" --out /tmp/shots --layouts 11
```

## 13. 최종 결론

- **자동 검증:** Gate G1–G7 모두 PASS. NOT_EXECUTED 0건, FAIL 0건, 전체 회귀 766/766, OTA 핵심 코드 무변경
- **하드웨어 검증:** 3건 대기

따라서 판정은 **PASS WITH HARDWARE VALIDATION PENDING**입니다.

---

# Part B — Zonal OTA Application Contracts (stacked on PR #1)

| 항목 | 값 |
|---|---|
| 대상 | `feature/zonal-application-contracts` (기준 `ab76590` = PR #1 head) |
| 실행 명령 | `CAPSTONE_QT_DIR=… CAPSTONE_UPSTREAM_DIR=… CAPSTONE_CLUSTER_APP=… CAPSTONE_CLUSTER_FAULT_APP=… scripts/verify-cluster-ota-demo.sh --snapshot docs/verification/evidence/latest` |
| 자동 판정 | **VERIFICATION RESULT: PASS**: G1–G7과 A1–A9 모두 PASS, NOT_EXECUTED 0건 |
| Qt 없는 환경 | **INCOMPLETE (exit 2)**: 실행할 수 없는 항목만 NOT_EXECUTED이고 FAIL은 0건 |
| 최종 결론 | **PASS WITH HARDWARE VALIDATION PENDING** |

## B.1 구현 범위

- **Central Control 앱 (`apps/central-control/`):**
  - 0x100 heartbeat(1 s)와 0x200 차량 신호(100 ms)를 보냅니다.
  - heartbeat의 version/state는 `capstone-ota-ui-status`가 만든 `/run` 상태 파일에서 읽습니다. 하드코딩하지 않고, 알 수 없으면 heartbeat를 보내지 않습니다.
  - maintenance IPC(ApplicationIpc v1)를 제공하고, maintenance 중에는 정지 상태 신호를 계속 보냅니다.
  - maintenance 상태는 activate_trial 재시작 뒤에도 유지됩니다.
  - CAN 전송 오류나 잘못된 IPC 상대가 있어도 멈추지 않습니다.
- **Qt Cluster (`customization/overlay`):**
  - `QLocalServer`가 GUI 이벤트 루프 위에서 동작합니다.
  - `vehicle`/`functional` 값을 upstream 모델(속도계, RPM, 경고등 8개)에 쓰고, 같은 모델에서 다시 읽어 응답합니다.
- **결함 빌드(DEMO/TEST FAULT INJECTION ONLY):**
  - `qmake CAPSTONE_FAULT_SPEED_DIVISOR=10`으로 빌드하면 속도 해석 경로 자체가 1/10이 됩니다. 표시와 응답이 함께 틀립니다.
  - 일반 payload 스크립트는 결함 바이너리를 거부하고, 결함 payload에는 라벨이 붙습니다.
- **OTA 프레임워크 변경 0줄:** coordinator, zone agent, CAN 코덱, validator, slots, `ui_status.py`. 보호 경로 diff를 G6/A9에서 자동으로 확인합니다.

## B.2 결과

| 항목 | 값 |
|---|---|
| 검증 요구사항 | 전체 48건(신규 VR-APP 23건): automated 34, conditional 11(Qt/실제 앱 필요), hardware 3 |
| 추적성 | 요구사항 48 ↔ 자동 테스트 노드 148개 ↔ 결과 (`evidence/latest/TRACEABILITY_MATRIX.md`) |
| pytest | **856 passed, 0 failed, 0 skipped** (PR #1 766개 + 이 브랜치 신규 90개) |
| Qt Test | AppStatus 38/38, ClusterSignals 28/28(일반 빌드), 28/28(결함 빌드), ClusterIpcServer 7/7 |
| QML TestCase | 14/14 |
| 패치된 upstream 빌드 | 일반·결함 두 변형 모두 `-Werror` 빌드 성공. 결함 marker는 결함 바이너리에만 존재 |
| Gate | G1–G7, A1–A9 모두 PASS |

### B.2.1 무하드웨어 시스템 E2E (실제 앱 + 실제 coordinator/zone agent + 가상 CAN)

| 시나리오 | 결과 |
|---|---|
| 정상 업데이트 (실제 Central + 실제 Qt) | `COMMITTED`, functional 검사 전부 통과, Cluster가 slot A → B로 재실행, maintenance는 실제 IPC로 [on, off] |
| 결함 빌드 trial | `FUNCTIONAL_VALUE_MISMATCH`(기대 650, 실제 65) → 두 ECU 모두 rollback → recovery functional 통과 → Cluster가 slot A로 재실행, 배지는 stable 1.0.0 → trial 1.1.1 → stable 1.0.0(restored 표시) |
| 검증 중 전원 차단 | 부팅 후 `ROLLED_BACK`, recovery 통과, 두 ECU slot A |
| trial 중 Central identity 손실 | `HEARTBEAT_*` 검출 → rollback → STABLE heartbeat로 recovery 통과 |
| 실제 Central + harness Cluster (Qt 불필요, CI에서 실행) | `COMMITTED`. Central이 실제로 재시작된 뒤 VERIFYING 시점에도 maintenance가 켜져 있음 |

**민감도(mutation) 확인:**
- request echo 변이 → Qt 테스트 FAIL
- 결함 빌드 자리에 일반 바이너리 → E2E가 COMMITTED되어 FAIL
- maintenance 소실 변이 → E2E FAIL
- 소켓 deadline 해제 또는 0666 권한 변이 → 통합 테스트 FAIL

## B.3 이 브랜치에서 발견하고 수정한 결함

| ID | 발견 방법 | 결함 | 조치 |
|---|---|---|---|
| DA-1 | 첫 Qt 컴파일 | 매개변수 이름 `signals`가 Qt 매크로와 충돌 | 이름 변경 |
| DA-2 | Qt 서버 테스트 | Qt가 소켓을 0770으로 만들어 명세(0660)와 다름 | listen 후 chmod 0660, 테스트로 고정 |
| RA-1 | 독립 리뷰 | Central 재시작(activate_trial) 시 maintenance가 사라지는데, E2E가 같은 객체를 재사용해서 이를 가림 | 상태 파일과 `RuntimeDirectoryPreserve=restart`; E2E가 재시작마다 새 객체를 만들고 VERIFYING 시점 maintenance를 확인 |
| RA-2 | 독립 리뷰 | 2 KB짜리 중첩 JSON이나 접속 폭주(EMFILE)로 Central이 죽음 | `RecursionError` 처리, 연결별 예외 격리 |
| RA-3 | 독립 리뷰 | 일시적인 CAN 전송 오류(ENOBUFS/ENETDOWN)로 Central이 죽음 | 해당 프레임만 건너뛰고 counter 연속성을 유지하며, 로그는 빈도를 제한해 기록 |
| RA-4 | 독립 리뷰 | Qt가 없을 때 verifier가 INCOMPLETE 대신 FAIL을 냄. Qt 없이 늘 실행되는 Central 증적이 conditional 항목에 묻힘 | Gate 규칙 수정, automated와 conditional 측정 항목 분리 |
| RA-5 | 독립 리뷰 | heartbeat counter 255→0 넘김이 테스트되지 않음 | 300 heartbeat 테스트 추가 |
| DA-3 | push 전 QA 점검 | 증적 JSON에 pytest 임시 경로(사용자 이름 포함)가 기록됨 | 파일 이름만 기록하도록 수정, 증적에 절대경로가 있으면 실패하는 테스트 추가 |

## B.4 잔여 위험과 한계

- **gear:** upstream에 기어 표시가 없어서 gear는 해석된 상태로만 보관하고 보고합니다. 속도, RPM, 경고등은 모델을 거쳐 검증됩니다.
- **Pi의 upstream 센서 루프:** Pi에서 upstream `Dash`의 센서와 경고등 타이머(ADC, 펄스 카운터, MCP23017)가 같은 모델을 덮어쓸 수 있습니다. functional은 같은 이벤트 루프 차례 안에서 쓰고 읽으므로 영향이 없지만, 화면에 Central 신호가 계속 유지되는지는 하드웨어에서 확인해야 합니다.
- **보류한 리뷰 Minor:**
  - selector 전환과 재시작 사이의 짧은 identity 구간
  - Qt 서버 소멸 순서(시스템 SIGTERM 경로에서는 해당 없음)
  - bind 중 process-wide umask
  - Qt 서버 테스트의 symlink/FIFO 케이스
  - `main()` 종료 경로 테스트
- **환경 차이:** 데스크톱 x86_64 Qt 5.15.2와 가상 CAN에서 확인했습니다. 실제 SocketCAN, MCP2515, ARM, EGLFS는 실행하지 않았습니다.

## B.5 남은 하드웨어 검증

| 항목 | 상태 |
|---|---|
| VR-HW-001: 두 Pi 시연(정상 commit, 결함 rollback, 전원 차단) | **PENDING**. 절차는 [CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md](../CLUSTER_STATUS_HARDWARE_ACCEPTANCE.md) Part 2 |
| VR-HW-002: 패치 전후 계기판 동작과 Central 신호 표시 유지 | **PENDING** |
| VR-HW-003: Pi 빌드(일반·결함), `run-qt-tests.sh` | **PENDING** |

표준 참고 범위는 Part A와 같습니다(Automotive SPICE 4.0 SWE.4/5/6·SUP.1, ISO 26262-6 검증 계층, UN R156 업데이트 검증·복구 개념). **어느 표준에도 준수, 인증, Capability Level, ASIL, 승인을 주장하지 않습니다.**
