# 2026-11-03 검증 결과

실제 Raspberry Pi 두 대에서 Ethernet OTA, MCP2515 CAN, native ARM Qt, systemd 앱 실행을 연결하여 검증했다. 세 번째 Pi의 CAN 배선·sudo 설정은 남아 있으므로 독립 세 보드 완료로 표현하지 않는다.

## 결과

| 실행 | 결과 | 시간 / 범위 |
| --- | --- | --- |
| 최종 PC pytest | **127 passed, 9 skipped, 실패 0** | 104.81초. hardware 옵션 없는 실행의 9개 skip은 아래 실물 실행에서 별도 수행 |
| 최종 실물 pytest | **9 passed, 실패·skip 0** | 219.95초. run ID `run-20261103-223306-368414` |
| Python coverage | 합산 약74%, statement 75.12%, branch 68.71% | capstone_ota만, C++/QML·배선과 별도. subprocess 경로 일부 미수집; 100% 주장 안 함 |
| 실제 CAN | 양방향 3개씩 정확 바이트 일치 | 두 보드 ERROR-ACTIVE, RX/TX 오류·bus-off 등 카운터 모두 0 |
| CAN 부하 | 관찰 출력 193~199 frames/s, 약5% | 5ms 요청, 검증 약5.87초 전후 cangen active 확인. 포화·장기 endurance 아님 |
| Ethernet PCAP | 24 GET / 24 HTTP200 | OTA 8회 × release.json/release.sig/app.tar.gz 각8회. 응답을 요청 프레임에 연결 |
| 초기화 최소권한 | 앱 UID995, 관찰된 초기화10회 모두995 | root 업데이터 계정으로 앱 --check 실행하지 않음 |

## 입증한 동작

- 최신 입력만 보내면 V3 업데이트가 정상 확정된다.
- 같은 CAN ID의 과거 정상 CRC 프레임이 재전송될 때 V3 표시가 기대62에서45로 덮인다. 검증 실패 후 V2를 실제 재시작·재검증하여 rolled_back이 된다.
- 정상 서명된 V1으로 복구 슬롯을 바꿔도 기록된 정상 V2 트리와 다르면 실행 전에 거부한다. min_svn2 유지, V1 실행 표식 없음. recovery_failed는 서비스 복원 성공이 아니다.
- 관찰 소켓이 없으면 새 버전을 확정하지 않는다.
- 실제 CAN에서 RPM/기어/경고, CRC 오류 거부, counter255→0, 오래된 counter 거부, 650ms 침묵, 새 입력 복구를 시험했다.
- 다운로드 bytes 기반 진행 표시와 복구 완료/복구 차단을 구분하는 native Qt 패널을 구현했다. 실보드 progress.json으로 PC 패널의 두 종료 화면도 렌더링했다. Pi HDMI 옆 패널 설치 완료 주장은 하지 않는다.

## 사용한 도구와 기록

pytest8.4.2, pytest-cov7.1.0, coverage7.17.1, allure-pytest2.16.2, Allure CLI3.22.1을 사용했다. 실제 can-utils(cansend/candump/cangen/canbusload), tcpdump4.99.5, tshark4.4.19를 실행했다. 결과는 JUnit, Allure JSON/HTML, coverage HTML/XML, CAN 원본, PCAP/HTTP TSV, Qt PNG와 TC CSV로 남긴다. Wireshark GUI로 PCAP을 열어 재분석할 수 있다.

GitHub Actions에도 JUnit·coverage·Allure 결과 업로드를 추가했다. 이 문서는 로컬 실행 결과이며 이번 커밋의 원격 CI 성공 여부는 별도 확인 대상이다. CI에는 물리 CAN 장비가 없고, native Qt가 준비되지 않은 테스트는 skip된다.

## 실패 조사와 개선

1. 최초 실물 실행 run1에서 정상 V2도 freshness에 실패했다. 매 프레임 SSH/Python 지연0.40~0.42초로500ms 유효기간을 넘기는 시험 도구 문제였다. SSH ControlMaster 재사용 후 warm 호출0.070~0.073초를 측정했다. 제품500ms를 유지한 채 재시험했다. 실패 원본과 측정 전후 JSON을 보존한다.
2. 서비스 초기화에 root 실행 가능성을 확인하고 runuser로 capstone-app을 강제했다. 실패 재현→수정과 실물 UID 시험을 남겼다. 기존 PC 서비스 순서 시험의 OS 대체 설정도 이에 맞춰 수정했다.
3. 별도 코드 검토에서 CAN 부하가 먼저 끝나도 통과할 수 있는 시험 허점을 발견했다. 최대30초의 단일 부하 서비스를 검증 전후 확인하고 finally에서 정지하도록 보강했다.
4. 사후 통계 수집 실패가 캡처 정리·V2 선택까지 건너뛰는 문제를 회귀시험으로 재현하고 정리를 독립적으로 시도하도록 수정했다. HTTP 성공 응답도 파일별 요청에 대응하도록 보강했다. 최종 Python 시험에 포함됐으며 수정된 정상 정리는 실제 보드에서 추가 실행했다.

## 종료 상태와 한계

별도 벤치 정리로 검증된 V2 device를 선택하고 Central의100ms 정상 CAN 송신을 시작했다. valid=true, model/QML65km/h, V2를 확인했다. Pi1 기존 digital-dash는 active로 보존했다. 이는 공격당한 device의 자동 서비스 복원 기능이 아니다.

관찰기는 Cluster와 같은 보드이고 모델/QML 값을 앱의 소켓에서 읽는다. PNG는 인증된 Xvfb 화면이다. 세 번째 CAN 관찰 보드, 독립 광학/HDMI, OS OTA, 전원 차단 복구, 장시간·포화 시험, 악성 앱의 자가 보고 위조 방어, V1 거부 후 자동 복원은 미검증/미구현 범위다. 시나리오는 구성한 수신 정책 회귀이며 실제 OEM 사고 재현이 아니다.

## 소스 추적

실물 실행은 HEAD3b14023 위 작업 트리에서 수행했다. ENVIRONMENT.json에 dirty 상태와 소스별 SHA-256을 기록했고 DEPLOYMENT-PROVENANCE.json에서 두 Pi에 설치된 core Python 파일이 최종 제어기 파일과 일치함을 확인했다. 하드웨어 실행 중 추가한 teardown 오류 경로 보강은 최종 전체 Python 시험과 별도 실물 cleanup 재실행으로 검증했다. 이후 결과 문서와 증적 목록을 확정하여 커밋한다. 원본 실험 기록의 HEAD를 나중 커밋으로 바꾸지 않는다.

재현은 [실행 안내](VERIFICATION_RUNBOOK.md), 요구사항·입력·기대값은 [시험 명세](TEST_CASES.md)를 따른다. 원본 증적은 노트북 Downloads/CAPSTONE_OTA_검증완료_2026-11-03에 별도로 보존한다. 개인키와 Wi-Fi 비밀번호는 포함하지 않는다.
