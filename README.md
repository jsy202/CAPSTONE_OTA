# 다중 ECU 환경에서의 OTA 소프트웨어 호환성 검증 및 자동 롤백 시스템

**CAPSTONE_OTA**는 디지털 클러스터 앱을 OTA로 설치하고, 시험 입력에 대한 실제
Qt 모델·QML 게이지 값을 검증한 뒤 업데이트를 확정하거나 이전 앱으로 되돌리는 프로젝트다.
복구 슬롯이 과거 정식 이미지로 바뀌어 있으면 실행 전에 거부하는 공격 시험도 포함한다.

현재는 **PC에서 업데이트 전달 → 실제 Qt 검증 → V2 롤백 → V1 바꿔치기 차단까지 연결**했다.
세 Raspberry Pi용 실행 경로는 구현했지만, 현재 Qt 경로의 물리 세 보드·CAN·ARM·systemd
통합 검증은 아직 수행하지 않았다.

## 전체 구조

```text
노트북: 서명된 Cluster 앱 릴리스 제공
  │ HTTP
  ▼
Pi 1 · Central: 서명·해시 검증 → 임시 캐시 → Cluster에 전달
  │ HTTP 파일 전송 + SSH 설치 명령
  ▼
Pi 2 · Cluster: 자체 검증 → 앱 A/B 시험 설치 → 실제 Qt 계기판 실행
  │                                       ├─ PASS: 새 앱 확정
  │                                       └─ FAIL: 이전 앱 무결성 확인 → 복원·재검증
  │
Pi 3 · Verifier: 시험 입력 지시 + CAN 직접 관찰 + Cluster의 Qt 모델·QML 값 확인
```

- **업데이트 파일**은 LAN으로 전달한다. CAN은 Central → Cluster 차량 데이터용이다.
- **A/B는 앱 디렉터리 두 개**다. OS 파티션 OTA나 재부팅 기반 복구가 아니다.
- Central은 Cluster 릴리스를 전달하며, 이 과정에서 Central 자신의 앱은 교체하지 않는다.
- 검증기는 별도 프로세스/장치지만 표시 결과는 **Cluster 소프트웨어가 제공하는 관찰값**을
  읽는다. 독립 카메라·픽셀 검증이나 악성 Cluster의 허위 보고 방어는 아니다.

PC 전체 데모는 실제 HTTP 두 구간과 실제 Qt를 사용한다. SSH는 로컬 명령 실행기로,
CAN은 UDP 루프백으로 대체한다. 실제 장치 세 대를 시험했다는 의미는 아니다.

## 현재 시험 시나리오

### 1. 지연·재전송 입력에서 표시 오류 검출

검증기가 고정 합성 CAN 프레임을 송신하도록 지시한다. ID는 `0x200`이고,
속도·RPM·기어·경고·카운터·CRC를 담은 8바이트다.

1. 카운터 240/241/242로 40 → 45 → 60 km/h를 보낸다.
2. **650ms 동안 송신을 멈춰** Cluster의 500ms 입력 유효 시간 초과를 확인한다.
3. 새 프레임 `243 / 62 km/h` 뒤에 기존 프레임 `241 / 45 km/h`를 재전송한다.
4. 새 프레임 `244 / 65 km/h` 뒤에 기존 프레임 `242 / 60 km/h`를 재전송한다.
5. 단절·복구를 총 세 번 반복한다. 새 카운터는 증가하고 과거 프레임은 그대로 유지한다.

과거 데이터도 CRC가 정상이다. 잘못된 숫자를 넣거나 앱 내부에서 강제 FAIL을 만드는
대신, **최신 값이 유지되어야 하는데 과거 값으로 덮이는지** 확인한다.

| 입력과 앱 조합 | 실제 동작 | OTA 결과 |
|---|---|---|
| 최신 프레임만 전달 + V3 | 기대 표시와 일치 | `committed`: V3 확정 |
| 과거 프레임 재전송 + V3 | 기대 62 대신 45 표시 | V3 거부 → V2 복원·재검증 |
| 동일 재전송 + V2 | 과거 프레임 무시, 62 유지 | `rolled_back`: V2 복구 확인 |

V2는 `source_counter`로 오래된 프레임을 거르고, V3는 `arrival_order`로 도착 순서대로
반영한다. 현재 두 버전은 **같은 실행 파일에 서로 다른 정책 설정을 넣은 시험용 릴리스**다.
화면 오른쪽 아래는 V1 보라색, V2 파란색, V3 주황색으로 구분한다.

**시나리오의 한계:** 재전송은 검증기가 주입하며 실제 Central 버퍼 기능은 구현하지 않았다.
CAN 컨트롤러가 자동으로 과거 값을 재생한다는 가정도 아니다. 현재 직접 입증한 것은
지연·재전송 입력에 대한 수신 처리 회귀다. 이를 대표 ECU 호환성 사례로 유지·보완할지는
재검토 중이다. 실제 ECU 조합 비호환이나 일주일 사용 후 버그를 입증했다고 주장하지 않는다.

### 2. 복구 슬롯의 정식 V1 바꿔치기 차단

```text
A = 정상 V2, B = 시험 V3
        ↓ V3 표시 불일치 검출
시험 코드가 복구 슬롯 A를 정식 서명된 V1으로 교체
        ↓ 신뢰 상태 기록은 그대로 유지
복구 전 기록된 V2 앱 트리 해시와 실제 파일 비교
        ↓ 불일치
V1 실행 전 거부 → recovery_failed
```

시험은 V1의 서명이 유효하고 신뢰 기록이 바뀌지 않았음을 확인한다. V1 실행 시 생성되는
표식이 없다는 것도 확인한다. 직접적인 차단 원인은 `recovery application tree hash mismatch`다.

공격자가 복구 슬롯 파일을 쓸 수 있다고 가정한 **제어된 파일 교체 시험**이며 실제 침입이나
V1 취약점 악용은 아니다. 업데이터·공개키·신뢰 기록까지 바꾸는 관리자 공격은 방어하지 않는다.
**V1 차단 이후 정상 서비스를 되살리는 별도 복구 경로는 아직 구현하지 않았다.**
`recovery_failed`는 복구 거부이며, V2 서비스 정상 실행을 뜻하지 않는다.

## PC에서 실행하기

Linux와 Python 3.10+를 사용한다. 네이티브 계기판은 C++17, make, Qt 5.15 개발·런타임
라이브러리가 필요하고, 숨겨진 화면 캡처에는 Xvfb가 필요하다. MQTT·CA·Python Qt 바인딩은
사용하지 않는다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

먼저 [Qt 빌드 절차](docs/QT_CLUSTER.md#build-on-pc)에 따라
[고정 버전의 Volvo 원본](dashboard/qt_cluster/UPSTREAM.md)을 준비하고 빌드한다.
아래 `/PATH/Volvo240-DigitalDash`는 실제 원본 체크아웃 경로로 바꾼다.

```bash
# 시스템 Qt를 사용하는 경우
.venv/bin/python dashboard/qt_cluster/build.py --source /PATH/Volvo240-DigitalDash

# 매 실행마다 새로운 출력 디렉터리 사용
.venv/bin/python -m demo.qt_run run --output .demo/qt-complete-run
.venv/bin/python -m pytest -q
```

별도 위치의 Qt를 사용하면 빌드에 `--qt-prefix`를 지정하고 실행 환경의
`CAPSTONE_QT_PREFIX`도 맞춘다. 상세 명령은 [QT_CLUSTER.md](docs/QT_CLUSTER.md)에 있다.
PC x86 바이너리를 Pi에 설치하면 안 되며, 대상 아키텍처로 따로 빌드해야 한다.

데모 결과는 출력 디렉터리의 `result.json`, `screenshots/`에 저장된다. 테스트는 offscreen,
캡처는 별도 Xvfb에서 실행하므로 사용자 데스크톱에 시험 창을 반복해서 띄우지 않는다.
**컴파일된 Qt가 없으면 Qt 테스트는 skip된다.** Python 테스트 통과만으로 Qt 통합 검증을
완료했다고 볼 수 없다.

데모 서명 개인키는 출력 디렉터리의 별도 `keys/`에 생성한다. 개인키·SSH 키를 HTTP 제공
폴더나 Git에 넣지 않는다. 빌드·시험 결과는 `.demo/` 아래에 두며 Git에 포함하지 않는다.

## 코드와 문서 읽는 순서

| 코드 | 역할 |
|---|---|
| [demo/qt_run.py](demo/qt_run.py) | PC 전체 OTA·Qt·공격 시험 실행 |
| [relay.py](capstone_ota/relay.py) | Central 검증·캐시·Cluster 전달 |
| [cluster.py](capstone_ota/cluster.py) | Cluster 서비스와 외부 판정 연결 |
| [device.py](capstone_ota/device.py) | 앱 A/B 상태 관리·확정·복구 |
| [bundle.py](capstone_ota/bundle.py) | 릴리스 묶기·서명·해시·안전한 압축 해제 |
| [qt_source.py](capstone_ota/qt_source.py) | 차량 프레임 생성·송신 |
| [qt_verify.py](capstone_ota/qt_verify.py) | 결함 주입 순서·관찰·기대값 비교 |
| [main.cpp](dashboard/qt_cluster/main.cpp) | 실제 Qt 수신·모델·게이지 관찰 어댑터 |
| [cli.py](capstone_ota/cli.py) | `pack`, `forward`, `cluster-update`, `update`, `status` |

- **현재 Qt 시험·3대 연결:** [QT_CLUSTER.md](docs/QT_CLUSTER.md)
- **Central → Cluster 배포·설정:** [CLUSTER_OTA.md](docs/CLUSTER_OTA.md)
- **원본 Qt 출처·MIT 라이선스:** [UPSTREAM.md](dashboard/qt_cluster/UPSTREAM.md)
- **기존 단일 장치 서비스 설정:** [PI_SETUP.md](docs/PI_SETUP.md)

기존 `ota update`는 Central 예제 앱용 기본 OTA 경로다. 현재 Cluster 배포는
`ota forward` → `ota cluster-update`를 사용한다. 릴리스는 `release.json`, `release.sig`,
`app.tar.gz` 세 파일이며, 진입점은 `bin/app`, 초기화 검사는 읽기 전용 `bin/app --check`다.

## 검증 범위와 남은 일

| 구분 | 현재 상태 |
|---|---|
| 실제 HTTP·서명·Cluster 앱 A/B | PC 통합 시험 완료 |
| 실제 Qt 속도 모델·QML 비교, 실패 후 V2 복원·재검증 | PC 통합 시험 완료 |
| 정식 V1 복구 슬롯 교체 및 실행 전 차단 | PC 통합 시험 완료 |
| 세 호스트 명령·SocketCAN·외부 검증 경로 | 구현됨, 현재 Qt의 물리 세 보드 통합은 미검증 |
| 원본 계기판의 모든 신호 | 미연결 계기는 고정 시험 초기값 사용 |
| 대표 ECU 호환성 시나리오 | 현재 재전송 시나리오의 타당성 재검토 중 |
| 독립 화면 픽셀 검증·관리자 변조 방어 | 미구현 |
| V1 차단 뒤 정상 서비스 복구·OS OTA | 현재 범위에서 제외 |
| 장기 내구·실제 전원 차단 | 미검증 |

HTTP는 실험망에서 서명된 파일을 전달하는 용도이며 암호화·접속 인증을 제공하지 않는다.
상태 기록·공개키는 앱 슬롯 밖에 보관하지만 하드웨어 보호 저장소가 아니다.
새 브랜치를 받는 것만으로 기존 Pi의 `digital-dash.service`나 OS를 변경하지 않는다.

과거 단일 장치 검증 기록은 [VALIDATION.md](docs/VALIDATION.md), 초기 가상 시험은
[VIRTUAL_COMPATIBILITY.md](docs/VIRTUAL_COMPATIBILITY.md), 이전 CAN 벤치는
[THREE_PI_BENCH.md](docs/THREE_PI_BENCH.md)에 남아 있다.
[architecture.html](docs/architecture.html)도 초기 가상 시연 자료이며 현재 Qt 구현 설명과 구분한다.
기존 MQTT·다중 ECU 구현은 [legacy/](legacy/ARCHIVE.md)에 보존했으며 기본 실행 경로가 아니다.
