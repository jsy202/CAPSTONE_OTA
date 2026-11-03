# CAN·Qt·OTA 검증 실행 안내 · 기본 두 보드 모드

이 문서는 두 대의 Raspberry Pi에서 실제 CAN 프레임, 실행 중인 ARM Qt 앱, 서명된 애플리케이션 OTA와 복구 동작을 재현하는 절차다. 판정 기준과 입력은 [TEST_CASES.md](TEST_CASES.md), 구현은 `bench/physical.py`, `bench/verify_service.py`, `bench/evidence.py`, `tests/hardware/`에 있다. 실행 결과는 실행별 산출물로 확인한다. 이 문서에 적힌 예상 결과 자체는 합격 증적이 아니다.

현재 세 보드 실물 검증도 완료했다. 이 문서의 기본 두 보드 모드에 [세 보드 설정](THREE_BOARD_SETUP.md)을 적용하면 Pi3가 CAN을 직접 관찰한다. 최신 [결과](THREE_BOARD_RESULTS_2026-11-03.md)와 [지연 RCA](THREE_BOARD_LATENCY_RCA.md)를 함께 읽는다.

## 1. 검증 범위와 장비

| 역할 | 장비/주소 | 확인 범위 |
| --- | --- | --- |
| Central·CAN 송신원 | Pi1 `cluster-pi.local`, Wi-Fi `192.168.45.171`, Ethernet `10.77.0.1` | 실제 SSH 명령으로 CAN 송신, 서명 패키지 전달, Ethernet 캡처 |
| Cluster·시험 대상 | Pi2 `capstone-pi2`, Ethernet `10.77.0.2` | native aarch64 Qt, SocketCAN, A/B OTA, 로컬 모델/QML 관찰 |
| 제어기 | 저장소·pytest가 있는 노트북 | 임시 릴리스 HTTP 서버, 시험 순서 제어, JUnit/Allure·증적 수집 |
| 별도 검증기 | Pi3 `capstone-pi3`, Wi-Fi `192.168.45.223` | CAN500k·sudo 설정·직접 관찰 완료. 세 보드 모드에서 사용 |

두 CAN 모듈 설정은 MCP2515, **12 MHz**, IRQ GPIO25, SPI CE0, `can0` **500000 bit/s**다. 실제 배선·종단저항·전원 상태는 시험 전에 확인한다. 하드웨어 수정 시 기존 합격 결과를 새 배선의 증거로 재사용하지 않는다. Pi1의 기존 `digital-dash`는 별도 서비스로 보존한다.

송신원은 Central의 실제 CAN이다. Cluster의 별도 SocketCAN witness가 요청한 프레임 ID·길이·바이트를 확인하고, Unix socket으로 실제 Qt 모델과 QML 속성을 읽는다. **기본 두 보드 모드에서는 witness와 모델 관찰기가 Cluster와 같은 보드**에 있다. 세 보드 모드에서는 Pi3가 CAN을 직접 관찰한다. 독립된 세 번째 CAN 보드, 광학 계측기, 악성 앱의 자가 보고를 완전히 배제하는 관찰 구조라고 표현하지 않는다.

Qt는 `capstone-cluster.service`에서 `capstone-app` 계정으로 실행한다. `capstone-cluster-display.service`는 Xauthority로 인증된 Xvfb `:93`을 제공한다. PNG는 이 가상 화면의 렌더링 결과다. HDMI 출력, 실물 디스플레이의 픽셀·밝기·시인성 검증은 포함하지 않는다. OTA는 앱 번들 범위이며 OS·부트로더 OTA는 포함하지 않는다.

## 2. 실행 전 확인

현재 설치된 벤치 런타임은 세 보드의 `/home/otauser/capstone-ota-bench-1793549816`이며 Python은 그 아래 `venv/bin/python`이다. 새 소스로 시험한다면 설치 소스와 제어기 소스의 일치를 먼저 확인한다. `ENVIRONMENT.json`의 HEAD만으로 dirty worktree 변경까지 식별할 수 없으므로 실행 당시 `git status --short`, diff, 배포 기록도 함께 보존한다.

다음은 해당 호스트에서 실행하는 읽기 명령 예다. SSH 별칭·사용자는 외부 설정에 맞춘다.

```sh
hostname
ip -details -statistics link show can0
command -v cansend
command -v candump
command -v cangen
command -v canbusload
```

Cluster에서는 서비스와 식별 정보를 확인한다.

```sh
systemctl status capstone-can.service capstone-cluster-display.service capstone-cluster.service
systemctl cat capstone-cluster.service
id capstone-app
```

Central에서는 `tcpdump`, `tshark`도 필요하다. 관찰된 도구 버전은 Central can-utils 2023.03, tcpdump 4.99.5, tshark 4.4.19, Cluster can-utils 2020.11이다. 버전은 실행 때 다시 기록한다. 제어기에서 확인한 pytest 8.4.2, allure-pytest 2.16.2, pytest-cov 7.1.0과 Allure CLI 3.22.1은 서로 다른 도구다.

`deploy/can-bench/install-cluster.py`는 최초 설치용 root 작업이다. 이미 `selected-device`가 있는 경우 재설치를 거부한다. 기존 설치를 지우고 재실행하지 말고 설치 상태를 점검한다. CAN 서비스는 root 권한으로 can0를 초기화하며 부팅 시 장치 대기를 `JobTimeoutSec=20s`로 제한한다. 앱 초기 `--check`도 `runuser`로 `capstone-app`에 내려가야 하며 SEC-004가 UID 기록으로 확인한다.

벤치는 기존 시험용 Cluster 서비스와 Central 데모 송신원을 정지하고 독립 device root를 만든다. 사용 중인 시험 장비에서만 실행한다. 같은 제어기에서는 `~/.cache/capstone-hardware-bench.lock`의 `flock`으로 중복 실행을 막지만 여러 제어기 사이의 분산 잠금은 아니다. `pytest-xdist` 병렬 실행을 사용하지 않는다.

## 3. 외부 설정과 실제 시험 실행

비밀 없는 설정 예다. 실제 SSH 별칭·접근권한, 노트북의 보드에서 접근 가능한 IP, native ARM 바이너리 경로로 수정하여 저장소 밖에 둔다. `arm_binary`는 제어기에서 읽을 수 있는 ELF64 aarch64 실행 파일이어야 한다.

```json
{
  "central_ssh": ["ssh", "capstone-central"],
  "cluster_ssh": ["ssh", "capstone-cluster"],
  "runtime": "/home/otauser/capstone-ota-bench-1793549816",
  "laptop_ip": "192.168.45.100",
  "arm_binary": "/absolute/path/to/native/dashboard"
}
```

이 상위 설정 외에도 Central→Cluster 전달용 SSH 별칭과 **Cluster 검증기→Central `capstone-source`** 별칭이 실제로 동작해야 한다. 현재 벤치의 내부 대상 별칭은 소스의 `new_device()`와 설치 설정에서 확인한다. 제어기에서 SSH가 된다는 사실만으로 보드 간 SSH가 확인되지는 않는다.

개별 프레임마다 SSH 접속과 Python 프로세스가 필요하다. Cluster의 root가 사용하는 `capstone-source` 별칭에는 확인된 호스트 키와 전용 ControlMaster 설정을 적용한다. 현재 운영 설정은 `ControlMaster auto`, `ControlPath /root/.ssh/capstone-source-%C`, `ControlPersist 60`이다. 첫 접속과 이후 접속의 시간을 따로 측정한다. 인증키, 비밀번호, 전체 SSH 비밀 설정을 증적으로 첨부하지 않는다.

다음 명령은 저장소 루트에서 실행하는 형식이다. `/outside/repo/hardware.json`은 실제 외부 설정으로 바꾼다. 출력 디렉터리는 **매 실행 새 경로**여야 한다. 검증 extra는 준비된 가상환경에 설치한다.

```sh
python -m pip install -e '.[verification]'
python -m pytest tests/hardware/test_physical_can.py -v \
  --hardware-config /outside/repo/hardware.json \
  --hardware-output .demo/hardware-RUN_ID \
  --junitxml .demo/hardware-RUN_ID-junit.xml \
  --alluredir .demo/hardware-RUN_ID-allure
```

옵션 없이 실행하면 hardware 시험은 명시적으로 skip된다. `--hardware-config`를 지정한 뒤 설정 파일·보드·장비가 없으면 오류 또는 실패여야 한다. unavailable 장비를 skip으로 바꾸거나 가짜 결과를 채우지 않는다. 선택된 hardware 시험의 성공 수와 일반 Python 시험의 성공 수를 합산할 때 skip·error도 함께 표시한다.

Allure CLI가 설치된 현재 제어기에서는 아래 형식으로 HTML 보고서를 만든다. 해당 실행의 원본 results 디렉터리를 보존한다.

```sh
.demo/allure-cli/node_modules/.bin/allure generate \
  .demo/hardware-RUN_ID-allure \
  -o .demo/hardware-RUN_ID-report
```

Python 단위·통합 시험과 coverage는 실제 hardware 결과와 분리하여 수집한다. coverage 수치는 Python 실행 범위이며 native C++·QML·배선 커버리지로 해석하지 않는다.

```sh
python -m pytest tests -m 'not hardware' \
  --cov=capstone_ota --cov-report=term-missing \
  --cov-report=html:.demo/python-coverage \
  --junitxml .demo/python-tests-junit.xml
```

## 4. CAN·네트워크 도구의 역할

| 도구 | 실제 용도 | 읽어야 할 값 |
| --- | --- | --- |
| `ip -details -statistics link show can0` | 설정·오류 점검 | bitrate, state, RX/TX, bus error·dropped·bus-off 전후 변화 |
| `cansend` + `candump -L` | 양방향 정확 바이트 확인 | ID 123/124, 각 3개 payload의 순서·일치 |
| `candump -L can0` | 시험 전체 CAN 로그 | 송신·수신 시각, ID, 길이·payload |
| `cangen can0 -I 321 -L 8 -D i -g 5 -n 6000` | 제한된 무관 ID 부하 | 요청 간격 5ms, 최대6000개/30초, 검증 전후 active 확인 |
| `canbusload can0@500000 -r` | 실제 버스 부하 측정 | 측정한 부하 출력. 요청 fps를 부하율로 환산해 단정하지 않음 |
| `tcpdump` | Central eth0의 실제 패킷 | pcap 크기·기간·대상 10.77.0.2 |
| `tshark` | pcap HTTP 해석 | 실제 관찰된 TCP 포트, URI, GET, 응답 200 |
| pytest/JUnit/Allure | 실행 판정·탐색 가능한 증적 | node ID, 시간, 실패 traceback, JSON·PNG 첨부 |

수동 CAN 점검은 자동시험과 동시에 하지 않는다. 예를 들어 수신 보드의 `candump -L -n 3 can0,123:7FF`를 먼저 준비하고 송신 보드에서 `cansend can0 123#0011223344556677` 등 세 payload를 보낸다. 반대 방향은 ID 124로 반복한다. 자동 CAN-001의 입력 전체는 시험 명세를 따른다.

부하는 유한 시간·프레임 수로 제한한다. 캡처 서비스도 `RuntimeMaxSec=900`으로 상한이 있다. 현재 구현의 5ms는 요청상 약 200 frames/s이며 실측 일정성을 보장하지 않는다. 100 frames/s 확장은 별도의 10ms 프로필 실행과 결과가 있어야 주장할 수 있다.

## 5. 증적 묶음과 판정

| 파일/기록 | 의미 |
| --- | --- |
| `ENVIRONMENT.json` | 실행 ID, HEAD, ARM 바이너리 SHA-256, 두 보드 관찰 한계 |
| `CAN-001.json`, `CAN-003.json`, `CAN-004.json` | CAN 입력·판정·신호/부하 결과 |
| `SCENARIO-*.json`, `OTA-CAN-002-*.json`, `SEC-003.json` | baseline/candidate status, verification 배열, 실제 버전·복구 결과 |
| `SEC-004.json` | 앱 계정 UID와 초기화에서 관찰한 UID 목록 |
| `NET-001.json`, `ethernet.pcap`, `http-analysis.tsv` | OTA 트래픽 원본·HTTP 해석·요청/200 수량 |
| `central-can.log`, `cluster-can.log` | 두 인터페이스의 candump 원본. fixture 정리 때 내려받음 |
| `*-baseline-*.png`, `*-candidate-*.png` | 실제 native Qt 가상 화면 캡처 |
| JUnit XML, Allure results/report | pytest 시험 수·상태, 오류, 첨부 증적 |
| 별도 Git 상태·diff·배포 기록 | 실행 소스의 dirty 변경과 원격 설치의 추적 |

NET-001은 OTA마다 `release.json`, `release.sig`, `app.tar.gz` 요청을 확인한다. tshark의 요청 프레임 번호와 `http.request_in`으로 성공 응답을 연결하며, 각 파일의 성공 수가 OTA 횟수 이상이어야 한다. TSV 원본과 JSON `successful_files`를 함께 보존한다.

`recovery_failed`는 보안 음성 시험에서 **예상 결과일 수 있다**. 실제 서비스 복원이 완료됐다는 뜻은 아니다. signed V1 대체 공격 거부 후 자동 서비스 복원은 검증 범위에 없으며 다음 시험은 새 root/baseline으로 시작한다. 운영 복원은 선택 device, `state.json`, 실행 버전·서비스·검증 결과를 확인한 뒤 별도 수행한다.

시험 수는 pytest node 수로 센다. 파라미터 세 개는 세 시험이며 한 시험 안의 CRC·wrap·timeout 등의 assert는 별도 시험 성공 수로 부풀리지 않는다. 실행 소스가 바뀌면 `--collect-only`와 JUnit의 실제 수를 기준으로 한다.

## 6. 관찰된 장애와 재시험 원칙

초기 `.demo/hw-can-ota-run1`은 V2 baseline 검증에서 실패했다. 프레임마다 새 SSH·Python을 시작하면서 한 번의 송신 왕복이 약 0.40~0.42초였고, 관찰·대기까지 합쳐 유효 프레임 간격이 앱의 **500ms** 신선도 제한을 넘었다. 시험 전송 경로가 만든 실패를 앱 회귀로 오인할 수 있는 사례다.

원인은 타임아웃 제한을 느슨하게 하지 않고 전송 지연을 측정하여 분리했다. SSH ControlMaster 재사용 후 첫 호출은 약 0.40초, 이후 약 0.070~0.073초가 관찰됐다. 앱의 500ms 유효기간은 유지했다. 실패 원본을 지우거나 같은 출력 폴더에 덮어쓰지 않고 새 실행 ID에서 재시험한다. 이 수치는 해당 측정값이며 모든 네트워크에서의 보장이 아니다.

초기화 `--check`의 root 실행 문제도 실패 재현 후 `runuser`로 수정했고 UID 음성 시험을 추가했다. 최종 합격 여부는 새 실행의 SEC-004 결과를 확인한다. 작업 중 실행과 최종 통합 실행을 섞어 최종 성공으로 보고하지 않는다.

## 7. 제출·설명에 사용할 수 있는 내용

검증 직무 관점에서는 요구사항→TC ID→fault injection→원본 로그→판정→RCA→재시험의 연결을 보여 준다. 예를 들어 CAN replay 재현 입력과 `STALE_REPLAY_DISPLAY_MISMATCH`, V2 재검증 결과를 하나의 증적 흐름으로 설명할 수 있다. can-utils를 설치했다는 사실보다 바이트 일치, counter 경계, CRC 변조, 신선도 만료, 실측 부하를 어떻게 판정했는지 제시한다.

결과를 외부 공유할 때는 비밀번호·SSH 개인키·Wi-Fi 비밀·서명 개인키가 없는지 확인한다. 실행별 Ed25519 개인키는 HTTP 제공 경로 밖의 임시 디렉터리에 생성되어 정리 시 삭제된다. 공개키·서명·시험용 릴리스와 최소한의 비밀 없는 증적만 보존한다.

미검증 항목은 HDMI/광학 판정, 전원 차단·부팅 중단, OS OTA, 장시간 내구성, 생산 환경 안전성·인증, 악성 앱의 모델 소켓 위조 방어, V1 차단 후 자동 서비스 복원이다. 구성한 송신원 계약 회귀를 실제 OEM 사고나 채용·인증 성과로 표현하지 않는다.

시험 종료 시 벤치 제어기가 별도로 검증된 V2 device를 다시 선택하고 Central의 정상 100ms CAN 송신 서비스를 시작한다. `BENCH-CLEANUP.json`의 valid·65km/h·V2를 확인한다. 이는 시험 장비 정리이며, 공격당한 device의 자동 서비스 복원 구현으로 세지 않는다.
