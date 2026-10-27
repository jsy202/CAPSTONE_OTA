# Central이 받아 Cluster에 전달하는 앱 OTA

```text
노트북: 서명된 Cluster 릴리스 제공
  │ HTTP, release.json + release.sig + app.tar.gz
  ▼
Central: 서명·압축파일 해시 확인 → 임시 캐시에 보관
  │ HTTP: 검증된 세 파일만 제공 / SSH: Cluster 설치 명령
  ▼
Cluster: 자체 공개키로 다시 확인 → 앱 A/B 시험 → 확정 또는 V2 복구
  └─ 외부 검사 사용 시 검사 실패로 롤백하고, V2도 다시 검사
```

Central 자신의 앱 슬롯은 바뀌지 않는다. 다운로드 서버와 SSH 명령은 Central에서
실행하고 A/B·공개키·보안 하한·복구본 해시는 Cluster 장치 경로에 저장한다.
CAN은 차량 데이터용이며 이번 구현에서 업데이트 파일은 LAN으로 전달한다.
일반 `ota update`와 과거 `demo.can_bench`는 Central 앱 시험 경로로 남아 있다.

## 준비

두 호스트에 이 브랜치를 설치한다. 기존 Pi의 Qt·OTA 서비스는 자동 변경하지 않는다.

```bash
python3 -m venv .venv
.venv/bin/pip install .
```

- 업데이트 서명 개인키는 노트북에만 보관한다.
- 신뢰하는 공개키를 Central과 Cluster에 각각 별도 경로로 설치한다.
- Central→Cluster SSH 계정/전용키/호스트 지문을 미리 설정한다. 예제의 `cluster-pi`는
  Central의 SSH 별칭이다. 실행은 BatchMode·StrictHostKeyChecking을 사용한다.
- Cluster가 Central의 지정 주소와 임시 TCP 포트에 접근할 수 있어야 한다.
  같은 격리 LAN에서 사용하고 방화벽 정책은 직접 맞춘다.
- HTTP는 서명된 내용만 인증하며 암호화나 송신자 접속 인증은 제공하지 않는다.

## 1. 노트북에서 릴리스 제공

기존 `ota pack`을 그대로 사용한다. payload는 실행 가능한 `bin/app`을 포함하고,
`bin/app --check`가 읽기 전용 초기화 검사 후 종료해야 한다.

```bash
.venv/bin/ota pack --payload /path/to/cluster-payload --output releases/cluster-v2 \
  --version 2.0.0 --svn 2 --key /PRIVATE_PATH/update.key
python3 -m http.server 8000 --bind 0.0.0.0 --directory releases
```

키 디렉터리를 HTTP로 제공하지 않는다. 이 명령은 Qt를 빌드하거나 기존 바이너리를
자동으로 payload로 바꾸지 않는다. Qt 앱에는 실제 초기화 검사와 실행 진입점이 필요하다.

## 2. Central에서 대상 설정

`deploy/cluster-target.example.json`을 별도 파일로 복사하고 실제 경로를 적는다.
경로는 **Cluster 안의 절대경로**이며 계정은 SSH 별칭에서 지정한다.
`root`를 Central 자신의 장치 경로나 기존 `/opt/digital-dash`로 지정하지 않는다.

예제의 `initialization_only: true`는 전달·설치·초기화만 검증하는 모드다.
외부 기능 검사로 승인하려면 이 항목을 제거하고 다음처럼 `check_command`를 넣는다.

```json
"check_command": ["/usr/bin/python3", "/home/otauser/trusted-checks/verify_cluster.py"]
```

이 명령은 **Cluster에서 실행**되며 선택된 슬롯의 절대경로를 마지막 인자로 받는다.
로컬 검사 프로그램이나 사전에 구성한 검증 Pi SSH 실행기를 지정할 수 있다.
후보 앱 안에 검사기를 넣지 않는다. `{"passed":true}` 한 줄과 종료코드 0만 승인한다.
false, 누락·깨진 출력, 비정상 종료, 60초 초과는 거부한다. systemd 모드에서는
앱 초기화·서비스 시작 후 검사하고 정상 롤백 후에도 같은 검사기를 다시 실행한다.
검증 Pi를 연결하는 실행기에는 이 남은 시간 안의 통신 제한시간이 필요하다.

실제 Qt 모델·원본 속도계 QML 값을 읽는 검사기와 지연 프레임 fault injection은
[QT_CLUSTER.md](QT_CLUSTER.md)에 연결했다. PC 전체 흐름은 검증하지만 실보드·픽셀
자동 판정과는 구분한다. 세 보드용 검증기 실행 경로도 같은 문서에 있다.

## 3. Central에서 전달

```bash
.venv/bin/ota forward http://LAPTOP_IP:8000/cluster-v2 \
  --public-key /home/otauser/update.pub \
  --target /home/otauser/cluster-target.json \
  --bind 0.0.0.0 --advertise CENTRAL_IP
```

Central은 노트북에서 세 파일을 모두 받아 검증한 뒤, 임시 HTTP 서버를 열고 SSH로
Cluster의 `cluster-update`를 실행한다. 서버에는 세 파일만 노출하고 설치 명령이
끝나면 서버·캐시를 정리한다. `--advertise`는 Cluster가 접근하는 Central의 IPv4/DNS
주소다. 기본 bind는 loopback이므로 실제 두 호스트에서는 위 설정이 필요하다.

결과에 `target: cluster`, `validation: initialization_only` 또는 `external`을 보고한다.
종료코드는 0=확정, 2=롤백/첫 설치 실패, 1=오류/복구 거부다. SSH가 끊겼다면 결과는
**미확인**이다. 타깃이 이미 확정했을 수 있으므로 먼저 Cluster 상태를 조회한다.

## 4. Cluster 상태와 중단 복구

Cluster에서 직접 실행한다. 복구에는 노트북이나 Central의 다운로드 서버가 필요 없다.

```bash
.venv/bin/ota --root /home/otauser/capstone-cluster-device status
.venv/bin/ota --root /home/otauser/capstone-cluster-device cluster-update --initialization-only
```

외부 검사로 설치했다면 복구 명령도 같은 `--check-command '["..."]'`를 지정한다.
검사 정책은 신뢰하는 배포 설정이다. 생략하고 초기화 전용으로 바꾸면 기능 검사도
생략되므로 운영 명령을 일관되게 유지해야 한다.

복구 슬롯을 서명된 옛 V1으로 바꾸더라도 기록된 정상 V2 파일 해시와 다르면
실행 전 거부한다. 기준 저널·업데이터·공개키까지 변조하는 관리자 공격은 방어하지 않는다.

## 서비스와 검증 범위

기본 모드는 서비스에 손대지 않고 설치 앱의 초기화/외부 검사만 수행한다.
선택적인 `systemd: true`는 첫 설치에서만 고를 수 있으며 **capstone-cluster.service**만
제어한다. 예제 unit은 별도 `/opt/capstone-cluster/device` 경로용이고 기존
`digital-dash.service`를 대체하지 않는다. Qt 디스플레이 세션·환경변수와 실제 앱 경로를
맞추기 전에는 이 unit을 설치하거나 활성화하지 않는다. 자동 부팅 복구도 포함하지 않는다.

로컬 테스트는 실제 HTTP 두 홉, 서명, 별도 Cluster CLI 프로세스, 외부 판정 거부와
재검사·롤백, V1 복구본 대체 차단, 잘못된 공개키, SSH 응답 유실을 검사한다.
SSH 전송만 로컬 실행기로 대체하므로 실제 호스트 인증이나 물리 세 Pi/CAN/Qt를
검증했다고 주장하지 않는다. 이번 실행에서 cluster-pi.local이 확인되지 않아 Pi에
배포하지 않았고 기존 서비스는 수정하지 않았다.
