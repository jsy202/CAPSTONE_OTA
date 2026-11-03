# 세 보드 최종 검증 결과 · 2026-11-03

Pi1 Central → Ethernet 앱 OTA → Pi2 native ARM Qt → Pi3 직접 CAN 관찰·원격 모델/QML 판정까지 실제 세 Raspberry Pi로 검증했다.

| 항목 | 실제 결과 |
| --- | --- |
| 실물 pytest | **10 passed, 실패·skip0, 320.56초** |
| PC pytest | **137 passed, 실패0, hardware 선택 전용10개 skip, 107.38초** |
| Python coverage | statements 74.43%, branches 66.47%. C++/QML·배선 및 별도 프로세스 일부는 별도 범위 |
| CAN | 세 보드 can0/500k, ERROR-ACTIVE, RX/TX 오류·bus-off 등 카운터 모두0 |
| 직접 통신 | Pi1→Pi2/Pi2→Pi1/Pi1→Pi3/Pi3→Pi2, 각3개 정확 바이트·순서 일치 |
| 정상 OTA | 최신 입력에서 V3 commit |
| 표시 회귀 | 같은 원본 과거 프레임 재전송에서 V3 기대62→실제45; V2 실제 재시작·재검증 후 rolled_back |
| 구버전 공격 | 진짜 서명된 V1으로 복구 슬롯 치환 → 실행 전 recovery_failed; old app 미실행 |
| 증적 부재 | 모델 소켓 부재와 Pi3 관찰 CAN 인터페이스 부재 모두 commit 금지, 로컬 fallback 없음 |
| 최소권한 | 초기화 UID 995, 10회 모두 동일 비root 계정 |
| 실제 HTTP | OTA10회, GET30/HTTP200 30, 각3파일 {'release.json': 10, 'release.sig': 10, 'app.tar.gz': 10} |
| replay 주입 | Pi3 로그에서 48쌍 확인, 실제 간격 10.097~15.678ms, 중앙값13.841ms |
| 부하 중 검증 | 9.69초 검증 전후 cangen active, 실제 canbusload 출력 보존. 포화/장기 내구 시험 아님 |

## 지연 결함과 근본 원인

첫 두 실행은 실패 원본까지 보존했다. [상세 RCA](THREE_BOARD_LATENCY_RCA.md)에 각 가설·분리 측정·입증·수정·재시험을 기록했다.

1. 명령 뒤 잔여 자식을 종료하는 process.exchange가 자동 생성된 SSH master까지 종료했다. 연결 재생성으로 관찰이500ms 근처 또는 그 이상 걸렸다. master 없음→snapshot0.794초→master 없음과, detached master 시작→snapshot0.175~0.246초→동일 master 생존을 실제 Pi3에서 비교했다.
2. 연결을 고쳐도 입력 사이마다 원격 관찰을 직렬 수행하면 새 프레임→과거 프레임 판정까지500ms를 넘겼다. V2가 과거 프레임을 거부하므로 유효시간이 갱신되지 않는 것은 정상이다. Central의 한 유한 명령에서 같은 두 프레임을10ms 간격으로 송신한 뒤 관찰하도록 수정했다. Pi3는 두 원시 프레임과 generation+2를 확인한다.

제품500ms, 단절650ms, valid=true, literal 기대62/65, V3 실제45/60 조건은 유지했다. Qt/SSH 응답을 가짜 합격으로 바꾸지 않았다. process.exchange의 자식 정리도 유지했다. 최종 원인2 수정 후10개 전체 실물 시험을 새 run에서 통과했다.

코드 검토에서는 Pi3 candump가 조기 종료돼도 무시하는 증적 허점을 찾아, 실제 자식 프로세스가3으로 종료하는 회귀시험을 RED→GREEN으로 수정했다. 조기 종료·빈 로그는 오류, stderr는 별도 저장한다. 두 프레임 source/계약과 gating 변경도 별도 검토했으며 남은 Important 지적은 없다.

## 종료 상태

벤치가 별도로 검증된 V2 device를 선택하고 정상100ms CAN 송신을 재시작했다. model/QML65km/h, valid=true, V2를 확인했다. Pi1 기존 digital-dash는 보존한다. 공격 device에서 V1 차단 후 자동 정상 복원 기능이 생긴 것은 아니다.

## 증적과 범위

세 보드 각각의 candump, PCAP·HTTP TSV, JUnit·Allure, Python coverage, Qt Xvfb PNG, TC CSV, burst timing과 소스 해시를 Downloads/CAPSTONE_OTA_3보드_검증_2026-11-03에 저장한다. 첫 두 실패와 SSH/Python/Qt 구간별 측정도 포함한다. 설치된 core 파일은 세 보드 모두 제어기 파일과 SHA-256 일치를 확인했다.

물리 CAN 관찰은 Pi3에서 독립적으로 수행하지만 표시값은 Pi2 앱의 모델/QML 소켓을 읽는다. 악성 앱의 자가 보고 위조·독립 광학/HDMI 검증은 포함하지 않는다. 인터페이스 부재 시험을 실제 케이블 단선 시험이라고 부르지 않는다. 종단저항 수·파형 품질은 오류0만으로 적합 판정하지 않는다. OS OTA·전원 차단·장기 endurance·생산차량 안전성/규격 인증·V1 차단 후 자동 서비스 복원은 별도 범위다. 구성한 replay 호환성 회귀를 실제 OEM 사고라고 주장하지 않는다.

환경 파일은 작업 당시 HEAD454922a 위 dirty 변경의 해시를 보존한다. 나중 최종 커밋으로 과거 실행의 HEAD를 바꾸지 않는다. 최종 커밋은 증적 묶음 SOURCE-COMMIT에서 확인한다.

재현 절차: [세 보드 설정](THREE_BOARD_SETUP.md), [시험 명세](TEST_CASES.md), [기본 도구 안내](VERIFICATION_RUNBOOK.md).

타이밍 해석:10ms burst 간격은 Pi3 하나의 candump 시계에서 계산했고 시험 경과는 monotonic 기준이다. 세 보드 절대시각 동기화와 서로 다른 보드 timestamp를 이용한 단방향 지연은 검증하지 않았다.
