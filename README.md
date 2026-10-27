# 다중 ECU 환경에서의 OTA 소프트웨어 호환성 검증 및 자동 롤백 시스템

저장소: **CAPSTONE_OTA**. 디지털 클러스터 앱을 업데이트하고, 다른 ECU의 입력에
대한 실제 Qt 표시 모델을 독립 검증한 뒤 확정하거나 이전 앱으로 되돌린다.

**Central이 받은 업데이트를 Cluster에 전달하고, Cluster에서 앱 A/B 설치·검사·롤백할 수 있다.**

```text
노트북 → Central: 서명·해시 확인 후 캐시
                  ↓ LAN 파일 전송 + SSH 설치 명령
                Cluster: 자체 검증 → 앱 A/B 시험 → 확정 또는 롤백
```

새 전달 경로는 [Cluster OTA 절차](docs/CLUSTER_OTA.md)의 `ota forward`와
`ota cluster-update`다. Central 앱은 교체하지 않는다. 초기화 전용 검사와 외부 검사
모드를 명시적으로 구분한다. [실제 Volvo Qt 검증·fault injection](docs/QT_CLUSTER.md)은
원본 속도계 QML 값과 C++ 모델을 읽어 판정하고, 실패 시 V2를 복구·재검사한다.
PC용 전체 실행은 `python -m demo.qt_run run --output .demo/qt-complete-run`이며
먼저 문서대로 Qt 앱을 빌드해야 한다. 실제 세 보드/CAN/ARM 검증은 아직 수행하지 않았다.
아래는 계속 지원하는 기존 단일 장치 OTA 사용법이다.

기본 흐름은 하나다.

```text
노트북: 패키지 만들기 → HTTP로 파일 제공
                            ↓ Wi-Fi
Pi: 다운로드 → 서명·해시 확인 → B에서 시험 실행
                                   ├─ 성공: B 확정
                                   └─ 실패: A 복구·재확인
```

여기서 A/B는 **앱 디렉터리**다. SD 카드의 OS 파티션을 나누거나 OS를 업데이트하지 않는다.
아래 일반 `ota update`의 대상은 Central 앱 하나다. 기존 Pi의 Qt 계기판·서비스는
이 브랜치를 받는 것만으로 바뀌지 않는다. Cluster는 위의 별도 전달 경로를 사용한다.

## 먼저 로컬에서 실행하기

Linux, Python 3.10+, OpenSSL이 필요하다. MQTT·Mosquitto·CA·클라이언트 인증서는 필요 없다.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

서명키는 한 번만 만든다. 개인키는 노트북에만 보관하고, Pi에는 공개키만 직접 전달한다.
이미 키가 있다면 아래 생성 명령을 다시 실행하지 않는다.

```bash
mkdir -p .demo
umask 077
openssl genpkey -algorithm ED25519 -out .demo/update.key
openssl pkey -in .demo/update.key -pubout -out .demo/update.pub
```

릴리스 만들기:

```bash
ota pack --payload examples/central --output .demo/releases/v1 \
  --version 1.0.0 --svn 1 --key .demo/update.key
```

다른 터미널에서 파일 서버를 켠다.

```bash
python3 -m http.server 8080 --bind 127.0.0.1 --directory .demo/releases
```

첫 터미널에서 설치하고 확인한다.

```bash
ota --root .demo/device update http://127.0.0.1:8080/v1 --public-key .demo/update.pub
ota --root .demo/device status
```

첫 설치는 A, 다음 업데이트는 B, 그다음은 A를 쓴다. 최초 설치한 공개키는 장치에 고정된다.
같은 장치의 이후 업데이트에는 `--public-key`가 필요 없다. 다른 키를 지정해도 바뀌지 않는다.

예상 상태:

```json
{"phase":"stable","active_slot":"A","version":"1.0.0","min_svn":1,"result":"committed","trial_version":null}
```

기본 로컬 모드는 실제 앱의 초기화 검사인 `bin/app --check`를 실행하지만, 상시 프로세스를 관리하지 않는다.
설치된 예제 앱을 직접 실행하려면 다음 명령을 사용한다.

```bash
.demo/device/current/bin/app
```

예제는 설정을 읽고 실행 상태를 유지하는 작은 앱이다. **CAN 송신·Qt 표시·ECU 호환성 검증은 아직 포함하지 않는다.**

## 실패하면 정말 돌아오는가?

잘못된 설정으로 시작하지 못하는 패키지를 만든다. 가짜 실패 판정 대신 앱 자체의 초기화가 실패한다.

```bash
cp -a examples/central .demo/bad-payload
sed -i 's/"period_ms":100/"period_ms":0/' .demo/bad-payload/config.json
ota pack --payload .demo/bad-payload --output .demo/releases/bad \
  --version 1.1.0 --svn 2 --key .demo/update.key
ota --root .demo/device update http://127.0.0.1:8080/bad
ota --root .demo/device status
```

결과는 `rolled_back`, 버전은 계속 `1.0.0`, 보안 하한도 `1`이다. 업데이트 명령은 실패를 뜻하는 종료 코드 2를 반환한다.
이전 앱도 검사에 실패하거나 내용이 손상됐다면 복구 성공으로 보고하지 않는다.

## 코드 읽는 순서

| 파일 | 하는 일 |
|---|---|
| [cli.py](capstone_ota/cli.py) | `pack`, `update`, `status` 명령 |
| [bundle.py](capstone_ota/bundle.py) | 파일 묶기, 서명·해시용 메타데이터, 안전한 압축 해제 |
| [device.py](capstone_ota/device.py) | 다운로드 → 시험 → 확정 또는 복구 |
| [relay.py](capstone_ota/relay.py) | Central에서 검증 후 Cluster에 전달 |
| [cluster.py](capstone_ota/cluster.py) | Cluster의 서비스·외부 검사 연결 |

릴리스 파일은 딱 세 개다: `release.json`, `release.sig`, `app.tar.gz`.
JSON 내용도 세 항목이다: `version`(앱 버전), `svn`(보안 버전), `sha256`(압축 파일 해시).
앱 진입점은 항상 `bin/app`이며, 초기화 검사 `--check`는 읽기 전용이어야 한다.

```text
device/
├── trusted.pub       # 고정된 업데이트 공개키
├── state.json        # 정상 슬롯·시험 슬롯·보안 하한을 관리하는 단일 기록
├── events.jsonl      # 진행 로그
├── current → slots/A
└── slots/
    ├── A/
    └── B/
```

업데이트 프로세스가 시험 도중 중단됐다면, 다시 실행하기 전에 다음 명령으로 미확정 시험을 복구한다.
일반 `update URL`도 새 다운로드보다 먼저 이 복구를 수행한다. `status`는 읽기 전용이다.

```bash
ota --root .demo/device update
```

## 남긴 보호와 명확한 범위

- 받은 메타데이터의 서명을 확인한 뒤 내용을 사용하고, 앱 파일 해시도 확인한다.
- 낮은 보안 버전과 현재 이하 제품 버전을 거부한다. 보안 하한은 성공 확정 때만 높인다.
- 복구 시 기록된 정상 앱의 파일·경로·권한 해시를 다시 확인한다.
- 슬롯 선택 전 시험 의도를 저장한다. 다음 실행은 미확정 시험을 복구한다.
- HTTP는 **실험망에서 설정을 줄이기 위한 선택**이다. 파일 인증은 유지하지만 통신 비밀성·상대 인증·서비스 가용성을 제공하지 않는다.
- 다운로드는 파일별 30초 전체 제한을 적용한다. 이 명령은 Linux의 메인 스레드에서 실행하며, 스레드에 넣어 쓰는 라이브러리 API는 제공하지 않는다.
- 서명은 앱이 안전하다는 증명은 아니다. 현재 검사는 앱 초기화이며 독립 ECU 검증이 아니다.
- 기록과 공개키는 앱 슬롯 밖에 있지만, 관리자/디스크 전체 변조를 막는 하드웨어 보안 저장소는 아니다.
- 슬롯은 앱 전용 읽기 데이터로 취급한다. 앱의 변경 가능한 데이터는 슬롯 밖에 저장해야 한다.

## 테스트와 Pi

```bash
python -m pytest -q
```

테스트는 실제 HTTP 전송·실제 서명·실제 실행 파일과 파일시스템을 사용한다. 정상 설치, 실패 복구,
변조, 보안 하한, 프로세스 중단 경계를 확인한다. 실제 Pi 전원 차단이나 systemd 동작을 대신 증명하지는 않는다.
실행 결과와 구현 판단·남은 한계는 [검증 기록](docs/VALIDATION.md)에 정리했다.

Pi에서 상시 서비스로 쓰는 선택 절차는 [PI_SETUP.md](docs/PI_SETUP.md)에 있다. 현재 실행 환경을 자동으로 이전하지 않는다.

기존 MQTT·다중 ECU 구현과 문서는 삭제하지 않고 [legacy/](legacy/ARCHIVE.md)에 보존했다.
기본 설치·명령·테스트에는 포함하지 않는다. 이후 호환성 검증과 취약 버전 복귀 차단 시나리오는 이 작은 흐름 위에 추가한다.

첫 가상 시연은 [재연결 호환성·복구 슬롯 바꿔치기 데모](docs/VIRTUAL_COMPATIBILITY.md)로 분리했다.
기본 OTA 코드는 그대로이며, 실제 서명·HTTP·A/B에 별도 프로세스 검증을 연결한다. 물리 CAN·Qt 시험은 아니다.
세 Pi의 Central·Cluster·검증기를 따로 실행하는 CAN 구현은
[세 장치 시험 절차](docs/THREE_PI_BENCH.md)에 있다. 물리 세 장치 검증은 아직 진행하지 않았다.

[인터랙티브 아키텍처 설명](docs/architecture.html)은 내려받아 브라우저에서 열면 된다.
정상 업데이트·비호환 롤백·V1 바꿔치기 차단을 단계별로 볼 수 있으며, 인터넷 연결이 필요 없다.
