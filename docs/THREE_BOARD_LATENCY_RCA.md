# 세 번째 Pi 검증 지연 조사

## 현상과 보존 증적

첫 세 보드 실물 실행 `.demo/three-board-run1`에서 CAN 기본 통신은 통과했지만 정상 V2 baseline이 INPUT_FRESHNESS_FAILURE로 실패했다. model/QML 값은 입력과 일치했으며 일부 시점의 valid가 false였다. 원본 JUnit, console, JSON, 세 보드 candump, Ethernet PCAP을 보존한다. 후보 V3 결함으로 분류하지 않는다.

## 현재 확인된 사실

- Pi3 실제 CAN can0 500kbit/s, Pi1→Pi3 원시 프레임 수신 확인.
- Pi3→Central/Cluster 단순 SSH true 명령은 warm 기준 약22~30ms다. Cluster 최초 SSH는 약426ms였다.
- 동일한 실제 검증 모듈 경로에서 관찰 snapshot은 약0.499~0.620초, Central 송신은0.146~0.210초로 측정됐다.
- 최종 관찰이 약0.576초 걸린 사례에서 valid=false였다. 다른 사례도500ms 근처에 위치한다.
- 제품 입력 유효기간500ms는 변경하지 않았다.
- Pi3→Cluster는 Wi-Fi→Pi1→Ethernet의 SSH ProxyJump 경로다. Pi2의 Qt 모델/QML은 SSH를 거쳐 읽는다.

## 원인 상태

확인된 직접 원인: 실제 관찰 경로의 지연이 제품 입력 유효기간과 겹쳐 정상 샘플도 검사 시점에 만료될 수 있다.

근본 원인은 아직 분리 측정 중이다. SSH 전송, 원격 Python 시작·import, 로컬 Qt 소켓 응답 시간을 각각 측정한다. Wi-Fi·ProxyJump·Qt 중 하나를 근거 없이 확정하지 않는다. 정상 V2라는 이름만 보고 검증을 우회하거나 valid 검사를 제거하지 않는다.

## 추적 파일

- `.demo/three-board-run1-console.log`
- `.demo/three-board-run1-junit.xml`
- `.demo/three-board-run1/BASELINE-FAIL-*.json`
- `.demo/three-board-run1/{central,cluster,verifier}-can.log`
- `.demo/three-board-run1/ethernet.pcap`
- `.demo/three-board-rca/pi3-ssh-timing-before.json`
- `.demo/three-board-rca/pi3-verifier-timing.json`

최종 수정과 재시험 결과는 원인이 입증된 후 아래에 추가한다. 초기 실패 기록을 덮어쓰지 않는다.

## 근본 원인 확인과 수정

`process.exchange()`는 명령이 끝난 뒤에도 소유한 프로세스 그룹 전체를 종료한다. 이는 앱/시험 명령의 잔여 자식을 정리하는 의도된 동작이다. 그러나 원격 snapshot의 첫 SSH에서 자동 생성된 ControlMaster도 같은 그룹에 남으면 함께 종료된다. 그 결과 다음 snapshot마다 SSH 연결을 다시 맺게 됐다.

실제 Pi3 재현:

- master 없음 확인 → 자동 생성 상태 snapshot 약0.794초 → 직후 `ssh -O check`가 socket 없음으로 실패.
- 별도 `ssh -MNf`로 detached master 시작 → snapshot0.246/0.177/0.175/0.176초 → 직후 master PID2170이 여전히 실행 중.
- Qt 로컬 소켓 응답은 약1.2ms, 모듈 import 약76ms였다. 단순 warm SSH는26ms 수준이었다.

증적: `.demo/three-board-rca/{breakdown,ssh-master-root-cause}.json`.

실제 벤치 검증 게이트는 각 판정 직전에 Pi3에서 두 신뢰 SSH alias의 master를 확인하고 없으면 `ssh -MNf`로 분리 시작한다. 소유 자식 정리와 제품500ms 정책은 유지한다. SSH ControlMaster/ControlPath/ControlPersist 설정과 pinned host key가 선행 조건이다. 수동으로 검증기를 실행할 때도 같은 사전 연결 준비가 필요하다. 이는 입력/판정 우회가 아닌 제어 전송 경로 준비다.

master 수정 후 run2: latest 정상 업데이트까지 통과했지만 buffered 시험은 아래 스케줄 문제로 실패했다. 두 문제를 분리한다.

## 두 번째 실행에서 분리된 추가 원인: 시험 스케줄

SSH 재연결을 제거한 run2에서는 정상 latest-only V3까지 통과했다. 그러나 buffered 정상 V2는 새 프레임 후 옛 프레임을 시험할 때 valid=false였다. 각 입력 사이에 원격 관찰 두 번과 SSH 송신을 직렬 수행하여 새 유효 프레임부터 재전송 판정까지500ms를 넘겼다. V2가 옛 프레임을 무시했으므로 그 프레임은 신선도를 갱신하지 않는 것이 올바르다. 이 실패 역시 V2의 표시 회귀가 아니라 시험 도구의 입력 스케줄 문제였다.

수정: Central의 한 유한 명령에서 기존과 동일한 새 프레임→원본 과거 프레임을10ms 간격으로 전송하고 그 뒤 표시를 관찰한다. Pi3는 두 프레임 각각의 ID/DLC/원시 바이트/순서를 CAN에서 확인하며 Qt generation이 정확히2 증가했는지 검사한다. 650ms 단절 시험과500ms 제품 기준 및 valid=true 합격 조건은 유지한다. replay 데이터·CRC·카운터와 기대62/실패45는 동일하다.

새 기능은 단일 프레임 또는 정확히 두 프레임만 허용한다. 전체 입력을 송신 전에 검증한다. 네이티브 Qt 회귀시험에서 source_counter는62, arrival_order는45를 관찰하는 RED→GREEN을 수행했다. malformed burst의 부분 송신 거부도 UDP 소켓으로 검사했다.

원인1(자동 master 정리)과 원인2(직렬 시험 스케줄)를 구분한다. run1/run2 원본을 모두 보존하며 최종 재시험은 새 출력 디렉터리에서 수행한다.

## 최종 재시험

원인1/2를 모두 수정하고 `.demo/three-board-final`에서 실물10개 모두 통과(320.56초). PC 최종137개 통과, hardware 선택 전용10개 skip(107.38초). Pi3 CAN 원본에서48개 fresh/replay 쌍의 실제 간격10.097~15.678ms를 확인했다. 세 보드 CAN 오류0, 모델 관찰·Pi3 CAN 부재 음성 시험도 통과했다. 최종 결과 보고서와 원본 JUnit을 함께 보존한다.

타이밍 해석:10ms burst 간격은 Pi3 하나의 candump 시계에서 계산했고 시험 경과는 monotonic 기준이다. 세 보드 절대시각 동기화와 서로 다른 보드 timestamp를 이용한 단방향 지연은 검증하지 않았다.
