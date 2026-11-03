# 실제 CAN·Qt·OTA 시험 명세

아래 기대값은 코드의 고정 oracle과 요구사항을 연결한다. 실제 결과는 실행 ID별 JSON/JUnit/Allure와 원본 CAN/pcap에 기록한다. 이 표는 완료·합격 선언이 아니며 실행하지 않은 항목은 `미실행`, 환경 불가로 선택하지 않은 hardware 시험은 `skip`, 선택 후 환경 문제는 `error/fail`로 구분한다. 실행 방법과 범위는 [VERIFICATION_RUNBOOK.md](VERIFICATION_RUNBOOK.md)를 따른다.

## 공통 전제와 결과 양식

실제 두 보드 can0 500kbit/s, 별도 Central 송신원, Cluster native ARM Qt 서비스와 SocketCAN witness를 사용한다. V2는 source-counter 정책, V3는 arrival-order 정책이며 모두 같은 native 실행 파일에 서로 다른 번들 정책·라벨을 적용한다. 버전 V1=`1.0.0`/SVN1, V2=`2.0.0`/SVN2, V3=`3.0.0`/SVN2다. V3는 SVN 증가가 없어도 기능 검증을 통과해야 commit된다.

각 실행의 결과 양식:

| 필드 | 기록할 값 |
| --- | --- |
| 실행 식별 | run ID, 시작/종료 시각, HEAD·dirty diff·원격 배포 버전, 바이너리 SHA-256 |
| 환경 | 장비 역할, CAN 설정·전후 오류 카운터, 도구 버전, 가상 디스플레이 여부 |
| 시험 식별 | 아래 TC ID, pytest node ID, 요구사항, 실행 순서 |
| 입력/기대 | 실제 ID·payload·counter·CRC·시간·정책, 고정 기대값 |
| 실제/판정 | JSON의 observed·status·UID·부하/HTTP 수치, pass/fail/error/skip |
| 증적/분석 | JSON·PNG·CAN log·pcap·JUnit/Allure 경로, 실패 원인, 수정·재시험 ID |

테스트 함수 수, 파라미터를 포함한 pytest node 수, 함수 안 assertion 수를 구분한다. CAN-003의 여러 경계 assert를 여러 독립 hardware 시험으로 세지 않는다. 세부 ID의 추적은 아래 표를 사용한다.

## CAN 기능과 관찰

| TC ID / 요구사항 | 입력·절차 | 기대 결과 | 결과·증적 필드 / 자동화 |
| --- | --- | --- | --- |
| CAN-001 / 양방향 물리 통신 | Central→Cluster ID `0x123`, 반대 `0x124`. 각 방향 payload `0011223344556677`, `1020304050607080`, `AABBCCDDEEFF1122`를 순서대로 전송. 수신 필터 준비 후 각 3개 캡처 | 두 방향 모두 정확히 3개 바이트·순서 일치, 수신 종료코드 0 | `CAN-001.json`: directions의 expected/actual/candump/stderr/passed. `test_CAN_001_actual_two_way_frames` |
| CAN-003-01 / 정상 신호 | V2에 speed raw600(60), counter255, rpm2200, gear3, warnings1 | model/QML speed60, rpm2200, gear3, warning true | `CAN-003.json`, 원본 `0x200` 로그. `test_CAN_003_crc_wrap_timeout_and_signal_models` 내 assert |
| CAN-003-02 / CRC 오류 거부 | raw2000, counter0 프레임의 CRC 마지막 바이트를 XOR1로 변조 | speed60·source_counter255 유지 | 동일 시험, 실제 CAN witness는 변조 바이트가 전송됐는지 확인 |
| CAN-003-03 / counter wrap | 정상 CRC raw610(61), counter0, rpm2300, gear4, warnings0 | 255→0 수용, speed61, rpm2300, gear4, warning false | 동일 시험, `checks`에 wrap 포함 |
| CAN-003-04 / 오래된 입력 거부 | wrap 후 raw350(35), counter254 전송 | speed61·source_counter0 유지 | 동일 시험. CRC가 유효한 오래된 프레임도 거부 |
| CAN-003-05 / 신선도 만료 | 유효 입력 후 650ms 침묵. 제품 유효기간500ms | snapshot.valid false | 동일 시험. 도구 지연을 실제 침묵 입력과 구분 |
| CAN-003-06 / 새 입력 복구 | raw620(62), counter1 | valid true, QML speed62, label V2 | `CAN-003.json` final에 실제 값 보존 |
| CAN-004 / 무관 ID 부하 | Central `cangen` ID0x321, 길이8, 증가 payload, gap5ms, 최대6000개/30초. 검증 전후 송신 서비스 active를 확인하고 finally 정지. 동시에 실제 앱 검증·`canbusload` 약4초 | 앱 검증 passed, 검증 전후 송신 active, 실제 부하 출력 존재 | `CAN-004.json`: verification, profile, load_tool, load_stderr, load_active_before_and_after, verification_elapsed_s. `test_CAN_004_unrelated_traffic_with_measured_load` |
| CAN-004-EXT / 100fps 확장 | 별도 gap10ms 프로필로 유한 프레임 송신·실측 | 같은 앱 판정과 실제 부하 확보 | 현재 5ms 자동시험과 구분. 별도 실행 증적 없으면 미실행 |

CAN-003의 assert 중간에 실패하면 함수 마지막의 종합 JSON이 생성되지 않을 수 있다. 그 경우 pytest traceback과 전체 CAN 로그를 원본 증거로 남긴다. `checks` 문자열 목록만으로 각 중간 실제 수치가 모두 저장됐다고 주장하지 않는다.

기본 프레임은 표준 CAN ID `0x200`, DLC8이다. 실제 신호 인코딩과 CRC 생성은 송신 코드 및 native 수신 코드를 함께 확인한다. CAN witness가 요청과 다른 바이트, 예상 외 추가 프레임, 미수신을 감지하면 시험을 실패시킨다.

## OTA 기능 검증과 복구

모든 시나리오는 새 device root에서 V2를 먼저 OTA하여 `baseline.result=committed`인지 확인한다. baseline 실패는 후보 V3 회귀로 판정하지 않고 `BASELINE-FAIL-*`과 원인을 보존한다. 정상 시나리오의 입력은 40(counter240)→45(241)→60(242), 이후 650ms 침묵→62(243)→65(244)를 반복한다. buffered 모드에서는 62 이후 처음의 45(counter241), 65 이후 처음의 60(counter242)를 **원래 payload 그대로** replay한다. 고정 기대값은 각각 62·65 유지다.

| TC ID / 요구사항 | 입력·절차 | 기대 결과 | 결과·증적 필드 / 자동화 |
| --- | --- | --- | --- |
| OTA-CAN-002-compatible / 정상 commit | V2 baseline, latest-only 송신원에서 서명된 V3 trial | candidate result committed, version3.0.0, validation external, 실제 SocketCAN 검증 passed | `OTA-CAN-002-compatible.json`, `SCENARIO-compatible-*.json`, verification의 observed와 PNG. `test_OTA_CAN_002_real_service_update_and_recovery[compatible-committed-3.0.0]` |
| OTA-CAN-002-mixed / 회귀 검출·기능 복구 | V2 baseline 후 V3, buffered replay 입력 | V3는 62를 유지해야 할 때 model/QML45로 되돌아 실패. V2를 다시 실행·재검증한 뒤 rolled_back, version2.0.0 | verification passed 배열 `[false,true]`, 최초 mismatch expected62/model45/qml45, result rolled_back, validation external. mixed 파라미터 |
| OTA-CAN-002-attack / 진짜 서명된 구버전 복구 차단 | V3 replay 실패 후 trusted bench fixture가 복구 슬롯을 V1으로 치환. V1 서명·archive hash는 유효, trusted journal 불변 | recovery_failed, version2.0.0은 마지막 기록된 정상 버전, min_svn2 유지, V1 app 미실행. 현재 실행 앱이 V2로 자동 복원됐다는 뜻은 아님 | attack_fixture.old_signature_valid=true, trusted_journal_unchanged=true, old_version1.0.0, old_app_executed=false. attack 파라미터 |
| SEC-003 / 관찰 증적 없을 때 fail closed | V2 baseline 성공 후 model socket을 존재하지 않는 경로로 변경, V3 OTA·복구 시도 | 후보 commit 금지, recovery_failed, min_svn2 유지, version2.0.0 | `SEC-003.json`, candidate verification 오류. `test_SEC_003_missing_evidence_cannot_commit` |
| SEC-004 / 초기 실행 최소권한 | baseline·후보·복구에서 실행되는 app `--check` UID를 앱 소유 위치에 기록 | UID 목록이 비어 있지 않고 모든 UID==capstone-app UID, UID!=0 | `SEC-004.json`: app_uid/uids. `test_SEC_004_app_initialization_is_not_root` |

공격 fixture는 벤치 경로와 trial 상태로 제한한 시험용 동작이다. 제품의 OTA API 기능이나 외부 공격자의 실제 접근권한 재현으로 표현하지 않는다. `old_app_executed=false`는 이번 fixture의 marker를 통한 관찰이며 모든 형태의 코드 실행 불가능성을 증명하지 않는다.

Qt model과 QML은 앱이 제공하는 관찰 소켓에서 읽는다. 고정 기대값과 CAN witness는 시험기가 관리하지만 malicious QML이 속성·capture를 위조하는 상황까지 독립적으로 검증한 것은 아니다. PNG는 failure frame 또는 최종 frame의 가상 화면이며 모든 중간 시각의 영상 기록이 아니다.

## Ethernet 전달과 도구 증적

| TC ID / 요구사항 | 입력·절차 | 기대 결과 | 결과·증적 필드 / 자동화 |
| --- | --- | --- | --- |
| NET-001 / 실제 OTA HTTP 전달 | 전체 OTA 동안 Central eth0에서 Cluster10.77.0.2와의 TCP 캡처. 실제 관찰 포트를 HTTP로 decode | pcap이 비어 있지 않음. OTA마다 release.json, release.sig, app.tar.gz의 GET·200 전달 확인 | `ethernet.pcap`, `http-analysis.tsv`, `NET-001.json`의 http_requests/http_200_responses/expected_file_requests/analysis/tool_versions. `test_NET_001_actual_ethernet_http_exchange` |

NET-001은 요청 프레임 번호와 응답의 `http.request_in`을 연결하여 세 파일 각각의 200 응답 수가 OTA 횟수 이상인지 검사한다. 중복 요청을 금지하는 검사는 아니다. HTTP 관찰은 서명 검증을 대체하지 않는다.

## 알려진 실행 상태와 미검증 범위

초기 양방향 can-utils 점검에서는 각 방향 세 프레임이 일치했고 오류0이 관찰됐다. 이 결과는 당시 기본 연결 확인이며 후속 OTA·보안·부하 시험 전체 합격을 의미하지 않는다.

`.demo/hw-can-ota-run1`은 baseline V2 freshness 실패를 남겼다. 매 프레임 SSH/Python 오버헤드와 500ms 유효기간의 상호작용을 조사하고 SSH 연결 재사용 후 새 실행으로 검증한다. 실패 기록도 제출 증적에 포함한다. 진행 중인 run2나 최종 통합 실행의 결과는 해당 JUnit·JSON을 읽어 기록하며 완료 전에 최종 성공 수를 고정하지 않는다.

다음은 현재 TC의 합격으로 대체할 수 없는 항목이다: 세 번째 보드의 독립 CAN 관찰, 실제 HDMI/광학 판정, 전원 차단 및 저장 중 중단 복구, OS·부트로더 OTA, 장시간 endurance, V1 차단 후 자동 서비스 복원, 생산 차량/규격 적합성·인증. 100fps 부하도 별도 증적 없이 200fps 시험에서 수행된 것으로 기록하지 않는다.

## 세 보드 구성으로의 확장

[세 보드 설정](THREE_BOARD_SETUP.md)의 verifier 필드 두 개를 지정하면 CAN-001에 Pi1→Pi3(ID125), Pi3→Pi2(ID126)가 추가된다. 각 방향 입력은 동일한3개 payload다. CAN-003/004와 OTA 판정은 Pi3에서 실행하며 별도의 verifier-can.log를 보존한다.

추가 SEC-005는 baseline V2 성공 후 Pi3 observe_channel을 존재하지 않는 인터페이스로 바꾼다. 기대 결과는 후보 commit 금지/recovery_failed, 마지막 정상 기록V2 유지, verification 모두false다. 두 보드 모드에서는 이 시험을 명시적으로 skip한다.

현행 buffered 주입은 같은 새+과거 frame을10ms 간격 쌍으로 보낸 뒤 관찰한다. 각 쌍의 두 CAN 바이트/순서와 generation+2를 검증한다. expected62/65, 잘못된45/60, 500ms 유효기간·650ms 침묵은 동일하다. 원격관찰 대기 때문에 프레임 간 간격이 늘어나는 문제를 제거한 것이며 과거 데이터/CRC를 바꾸지 않았다. 과거 실행은 그 당시 직렬 주입 기록으로 보존한다.
