# Qt 비교·A/B 슬롯 패널 검증 결과 · 2026-11-04

추천안2(기준값/표시값 비교)와4(A/B 슬롯·복구 경로)를 기존 독립 Qt 패널에 구현했다. 추가 물리 버튼이나 GPIO 제어는 이 작업에 포함하지 않았다.

- PC 전체 pytest: **148 passed,10 hardware skips,111.18초**. 선택하지 않은 하드웨어 항목은 아래 실물 실행에서 별도로 수행했다.
- 실제 세 보드 pytest: **10 passed,0fail/skip,292.12초**. OTA/롤백/공격 차단 외에 UI에 쓰는 progress 비교값·선택 슬롯·V1 주입 기록도 assertion으로 검사했다.
- 실제 compatible/mixed/attack 기록을 native Qt로 PC/ARM 각각 렌더링: **3개 시나리오 모두 snapshot 동일**,6개 PNG 확인.
- ARM 한글 폰트 누락을 실제 PNG에서 발견했다. Noto CJK 설치·fontconfig 갱신 후 재캡처하여 네모 글자가 해결됨을 확인했다. 문자열 assertion 통과만으로 시각적 정상이라고 판단하지 않았다.

## 표시 동작

정상 업데이트는 최종 검증65=65와 B/V3 선택·검증 통과를 표시한다. mixed 시나리오는 후보 V3의 최초 기대62/실제45를 크게 유지하고, 별도로 복구 재검증65=65 및 B→A·V2 통과를 보여준다. attack에서는 A의 정상기록V2와 시험주입V1을 구분하며, 복구 실패·정상 서비스 미복원을 표시한다. 선택된 B/V3도 정상 승인으로 칠하지 않는다.

값은 판정에서 수집한 샘플이며 CAN 수신값을 연속 재생하는 숫자 계기가 아니다. 슬롯의 버전은 신뢰 기록이며 실제 파일을 UI가 새로 인증한 결과가 아니다. expiry로 valid=false인 동일값 샘플은 속도 차이 대신 입력 유효기간 초과로 표시한다.

## 검증과 리뷰에서 보완한 부분

- Progress 슬롯 정보는 telemetry 전용이며 OTA 보안 판정에 사용하지 않는다. 쓰기 실패·download deadline 관련 기존 회귀시험도 유지했다.
- 첫 candidate timeout에 실제 샘플이 없을 때, 후속 V2 recovery mismatch가 V3로 잘못 표시될 수 있다는 코드 리뷰 지적을 재현했다. 실패 row/slot/version/phase를 함께 최초 저장하도록 RED→GREEN으로 수정했다.
- 외국 PID를 노트북에서 검사하지 않는다. writer PID/age는 Pi2에서, 수신 freshness는 노트북에서 검사한다. 연결 끊김은 과거 성공 기록에서도 현재 정상 표시를 막는다.
- 없어지거나 잘못된 데이터, stale/future/dead writer, 복구 거부, 큰 비교값과 선택/검증 구분을 native QML 속성으로 시험했다. 기존10개 패널 시험도 유지했다.

## 실행과 증적

[실행 안내](QT_PROGRESS.md)의 remote 명령으로 시작한다. 현재 노트북 모니터는 `.demo/visual-live-final-20261104`에서 실제 Pi2 기록을 읽는다. 닫아도 원격 OTA는 중단되지 않는다.

증적은 Downloads/CAPSTONE_OTA_Qt_비교슬롯_2026-11-04에 보존한다. 실제 세 보드 결과·progress JSON, PC/ARM PNG·snapshot, 전체 JUnit/Allure·Python coverage, 관찰 이력, 폰트·설치 소스 해시, 실행 방법을 포함한다. 개인키나 비밀번호는 포함하지 않는다.

물리 HDMI 화면, 독립 카메라·픽셀 판정, OS OTA, 전원 차단·장기 내구, V1 차단 후 자동 서비스 복원은 기존 범위와 동일하게 별도다. ARM 그림은 Xvfb 렌더링이다. 이번 결과를 실제 차량/OEM 사고 재현이나 하드웨어 전체 인증이라고 주장하지 않는다.
