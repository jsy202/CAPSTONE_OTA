# 실제 세 보드 실행 구성

Pi1 Central은 업데이트를 Pi2에 전달하고 CAN 데이터를 송신한다. Pi2 Cluster는 실제 ARM Qt 앱을 실행한다. Pi3 Verifier는 **자기 can0에서 프레임을 직접 수신**하고 Pi2의 모델/QML 관찰값을 SSH로 읽어 판정한다. 모델/QML은 앱이 제공하므로 독립 픽셀 검증은 아니다.

| 역할 | 네트워크 | CAN |
| --- | --- | --- |
| Pi1 Central | Wi-Fi192.168.45.171, Ethernet10.77.0.1 | MCP2515/12MHz, can0/500k |
| Pi2 Cluster | Ethernet10.77.0.2, Pi1 경유 관리 | 동일 |
| Pi3 Verifier | Wi-Fi192.168.45.223 | 동일, 직접 관찰 socket |

OTA 파일은 Ethernet, 검증 제어·판정은 SSH, 차량 신호는 CAN이다. 세 보드가 있다고 CAN으로 OTA 파일을 전송하는 것은 아니다.

## 준비와 신뢰

Pi3 SD에 기존 Wi-Fi·SSH 키를 보존한 채 CAN overlay와 capstone-can.service를 추가했다. 초기 sudo 준비 누락은 오프라인 SD에서 바로잡았다. 비밀번호는 이 문서에 기록하지 않는다. 세 대 재부팅 뒤 SSH·CAN 재접속을 확인했다.

Pi3에서 전용 SSH 키를 만들고 공개키만 Pi1/Pi2에 설치했다. Cluster updater의 전용 공개키도 Pi3에 허용했다. 호스트 키는 이미 인증된 SSH 접속으로 읽어 고정했다. 노트북 개인키를 보드에 복사하지 않았다. Pi2의 관찰 계정 otauser는 capstone-app 그룹으로 모델 소켓에 접근하며 업데이트 journal은 root 소유다.

- Cluster root → `capstone-verifier`: Pi3에서 실제 검증 실행.
- Pi3 → `capstone-source`: Pi1 CAN 송신 명령.
- Pi3 → `capstone-cluster-observer`: Pi1 ProxyJump를 거쳐 Pi2 관찰 소켓 읽기.
- 각 연결은 전용 identity·StrictHostKeyChecking·고정 known_hosts를 사용한다.

두 Pi3 alias에 ControlMaster/ControlPath/ControlPersist가 필요하다. root bench checker는 Pi3에서 master 유무를 검사하고 없으면 `ssh -MNf`로 분리 시작한 뒤 실제 검증을 실행한다. 이 단계는 verifier가 소유한 단기 자식 프로세스를 정리해도 전송 연결이 유지되게 한다. 자세한 실패 재현은 [지연 RCA](THREE_BOARD_LATENCY_RCA.md)에 있다. standalone qt_verify 실행 시에도 전송 준비가 필요하다.

## 실행

기존 hardware 설정에 다음 **두 필드를 함께** 추가한다. 누락된 반쪽 설정으로 두 보드 관찰에 조용히 fallback하지 않는다. IP/SSH 접근과 ARM 바이너리 등 나머지는 [기본 실행 안내](VERIFICATION_RUNBOOK.md)를 따른다.

```json
{
  "verifier_ssh": ["ssh", "-F", "/absolute/path/pi3-ssh.conf", "capstone-pi3"],
  "verifier_peer": {
    "host": "capstone-verifier",
    "checkout": "/home/otauser/capstone-ota-bench-1793549816",
    "python": "/home/otauser/capstone-ota-bench-1793549816/venv/bin/python"
  }
}
```

```sh
.venv/bin/python -m pytest tests/hardware/test_physical_can.py -v \
  --hardware-config .demo/three-board-config.json \
  --hardware-output .demo/three-board-NEW_RUN \
  --junitxml .demo/three-board-NEW_RUN-junit.xml \
  --alluredir .demo/three-board-NEW_RUN-allure
```

현재 제어기의 외부 설정은 `.demo/three-board-config.json`이고 저장소에는 올리지 않는다. 보드 런타임은 기본 벤치와 같은 경로다. can-utils는 Pi3에도 설치했다. root 권한은 부팅 CAN 설정에만 필요하며 Pi3의 실제 raw CAN 관찰과 candump는 otauser로 실행한다.

## 세 보드에서 추가로 입증할 항목

- CAN-001: Pi1→Pi2, Pi2→Pi1 외에 Pi1→Pi3/ID125, Pi3→Pi2/ID126 각각3개 정확 payload.
- 모든 실제 OTA 판정의 transport는 `SocketCAN directly observed by verifier`.
- CAN-003/004: Pi3가 실제 신호·CRC·counter·timeout·부하를 관찰.
- SEC-005: 정상 baseline 뒤 Pi3 관찰 인터페이스를 존재하지 않는 이름으로 바꾸면 commit 금지. Pi2의 로컬 관찰기로 대체하지 않음.
- verifier-can.log를 별도 보존. 캡처가 일찍 종료되거나 비어 있으면 증적 수집 오류 처리.

재전송 시험은 Central에서 새 프레임과 원래 과거 프레임을10ms 간격의 유한 쌍으로 송신한다. Pi3는 두 raw프레임과 Qt generation+2를 확인한다. 단절650ms, 제품 유효기간500ms, 정상 V2 기대62/65와 V3 잘못된45/60은 유지한다.

CAN 배선과 종단저항은 물리적으로 확인해야 한다. 종단저항은 버스 양끝 두 곳이 기준이며 세 보드라는 이유로 세 개를 켜는 구성이 아니다. 현재 소프트웨어 시험의 오류 카운터0만으로 종단저항이나 전기 신호 품질 전체가 적합하다고 증명하지 않는다.

최종 결과: 실제 세 보드10개 모두 통과, PC137개 통과(hardware10개 skip). [결과 보고서](THREE_BOARD_RESULTS_2026-11-03.md)와 JUnit을 함께 확인한다. 초기 두 보드 결과는 이전 기록으로 보존한다. OS OTA·전원 차단·실물 HDMI·장기 내구성·V1 거부 뒤 자동 서비스 복원은 이 전환으로 구현되는 항목이 아니다.
