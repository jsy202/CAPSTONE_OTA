# 다중 ECU 환경에서의 OTA 소프트웨어 호환성 검증 및 자동 롤백 시스템

**CAPSTONE_OTA**는 IVI 업데이트가 같은 보드의 디지털 클러스터와 CPU를 두고 경합하여
클러스터 처리를 늦추는 상황을 감지하고, 이전 IVI 앱으로 자동 롤백하는 Raspberry Pi 프로젝트다.
독립된 Pi3가 CAN 입력과 클러스터 처리 응답을 직접 관찰한다.

현재 대표 시나리오는 **IVI CPU 경합으로 인한 자원·타이밍 호환성 문제**다.
이전 CAN 재전송·표시 오류 시나리오는 제거했다. 복구 슬롯 바꿔치기 공격 시험은 유지하며 IVI 패키지로 검증한다.

## 목차

1. [왜 검증하는가](#왜-검증하는가)
2. [현재 구조](#현재-구조)
3. [대표 시나리오: IVI V3의 CPU 경합](#대표-시나리오-ivi-v3의-cpu-경합)
4. [실보드 결과와 화면](#실보드-결과와-화면)
5. [코드 읽는 순서](#코드-읽는-순서)
6. [로컬 검증](#로컬-검증)
7. [보안 시나리오 세 가지](#보안-시나리오-세-가지)
8. [현재 한계와 남은 일](#현재-한계와-남은-일)

## 왜 검증하는가

업데이트한 앱 자체가 실행된다고 기존 기능과 함께 정상 동작한다는 뜻은 아니다.
같은 컴퓨팅 자원을 공유하면 새 기능의 CPU 사용과 실행 우선순위가 기존 기능의 응답 시간을
깨뜨릴 수 있다. 이 프로젝트는 설치 직후의 실행 확인에 더해, 시험 운영 기간 동안 기존
클러스터의 처리 지연·끊김을 감시하고 문제가 지속되면 업데이트한 IVI를 되돌리는 흐름을 구현한다.

Pi2의 IVI와 클러스터는 **한 Linux 보드에서 실행되는 별도 앱/서비스**다.
물리적으로 분리된 두 ECU가 아니라 통합 콕핏의 공유 자원 환경을 모사한다.
특정 제조사의 실제 결함이나 양산 시스템을 재현했다고 주장하지 않는다.

## 현재 구조

```text
노트북: 서명된 앱 릴리스 제공
  │ LAN 파일 전송
  ▼
Pi1 Central ── 검증·중계 ──▶ Pi2 Cockpit
  │                          ├─ 디지털 클러스터: CAN 수신 → Qt 모델 반영 → 0x300 응답
  │                          └─ IVI: 앱 A/B 시험 설치 → 시험 운영 → 확정 또는 롤백
  │ CAN 0x200 차량 입력                              ▲
  └───────────────── CAN 버스 ──────────────────────┤
                            │                      │ 판정 조회(SSH)
                            ▼                      │
                     Pi3 Verifier ─────────────────┘
                     0x200·0x300 직접 수신 → 지연·끊김 판정
```

- 실제 장치: Raspberry Pi 세 대, MCP2515 HAT(12MHz 발진기), `can0` 500kbit/s.
- OTA 파일은 LAN으로, 차량 입력과 처리 응답은 CAN으로 전달한다.
- **업데이트·롤백 대상은 IVI다.** Central과 클러스터는 이 시나리오에서 교체하지 않는다.
- 클러스터는 **버전·색상·수신 정책 분기가 없는 단일 구현**이다. V1·V2·V3는 IVI 릴리스 구분이다.
- **A/B는 앱 디렉터리 두 개**다. OS 파티션·커널·부트로더 OTA가 아니다.
- 릴리스는 `release.json`, `release.sig`, `app.tar.gz`로 구성한다.
  Ed25519 서명·파일 해시·복구 앱 트리 해시 확인과 기존 A/B 복구 경로를 재사용한다.

## 대표 시나리오: IVI V3의 CPU 경합

1. **V2:** 지도가 없는 IVI. 기존 클러스터가 정상적으로 CAN 입력을 처리한다.
2. **V3 설치:** 지도 기능이 추가되지만 지도가 닫혀 있으면 큰 부하가 없다.
3. **지도 열기:** V3가 CPU 계산 워커를 코어 수만큼 실행한다. Pi4에서는 4개이며,
   Linux `SCHED_RR` 실시간 우선순위 10을 사용한다.
4. **기존 기능 저하:** 같은 보드의 클러스터가 CPU 실행 기회를 늦게 받아 입력 반영이 밀린다.
5. **Pi3 감지:** 지연·끊김이 지속되면 이상을 확정한다.
6. **IVI 롤백:** Pi2 감독기가 정차 조건을 확인한 뒤 V2로 복구하고 다시 감시해 정상화를 확인한다.

**부하의 정체:** 직접 만든 Python 다중 프로세스 반복 계산이다. 실제 지도 타일 디코딩을
측정해서 재현한 부하가 아니며, 지도 화면 자체의 렌더링이 지연 원인이라는 주장도 아니다.
워커별 CPU 사용률 상한이나 코어 고정은 앱 코드에 없으며 워커 수와 우선순위로 부하를 조절한다.
일반 우선순위 부하에서는 문제가 재현되지 않아, 전 코어 실시간 부하 조건을 선택했다.

### 무엇을 측정하는가

- Pi1의 **`0x200`**: 속도·RPM·기어·경고·원본 카운터·CRC.
- Pi2의 **`0x300`**: Qt 속도 모델 반영 직후 보내는 속도·원본 카운터·유효 여부·시작 식별자·CRC.
- Pi3는 자기 SocketCAN 소켓에서 두 프레임을 받고, **같은 카운터의 입력과 응답**을 짝지어
  커널 수신 시각 차이를 계산한다. 보드 사이 시계 동기화나 SSH 왕복 시간으로 계산하지 않는다.
- 응답 누락, 응답 간 긴 간격, 정상 기준선 대비 지연 변화도 집계한다.

측정 대상은 **클러스터 처리 응답 지연**이다. 화면 픽셀이 실제로 그려진 시간은 아니며,
응답은 클러스터 소프트웨어의 자기 보고다. 악성 클러스터의 허위 응답 방어는 구현하지 않았다.
입력·응답 속도는 기록하지만 현재 감지 규칙은 지연·끊김 중심이며 값 불일치 판정은 포함하지 않는다.

### 판정과 롤백 조건

- CAN 수신은 계속 대기·처리하고 Pi3 결과 파일은 약 1초마다 갱신한다.
  Pi2 감독기는 기본 2초 간격으로 SSH를 통해 결과를 조회한다.
- 기본 판정 창은 **10초**, 이동 간격은 **5초**다.
- 주요 요구사항은 **창 p99 처리 지연 ≤ 100ms**다. 위반이 같은 유형으로 3개 창 연속 지속되면
  확정한다. 100ms는 CAN 입력 한 주기 안에 처리한다는 개발 기준이며 업계 공통 기준이 아니다.
- 정상 기준선 대비 p99, CUSUM, 드리프트, 응답 끊김·누락, 입력 주기 이상도 검사한다.
  선언 지연 기준 이외의 통계 규칙은 기본적으로 2개 이상 동시 발화와 지속 조건을 사용한다.
  같은 지연 데이터에서 계산한 통계들이므로 통계적으로 독립된 증거는 아니다.
- 정상 감시 시간이 충분하면 새 IVI를 확정한다. 이상이 확정되면 정차 창을 기다려 롤백한다.

**현재 예외 동작:** Pi3 관측이 끊기면 정차 판단을 Pi2 자체 버스 관측으로 축소한다.
정차 대기가 `max_wait_s`(기본 600초)를 넘으면 정차 확인 없이 롤백하는 폴백이 남아 있다.
따라서 현재 구현을 "항상 정차 후에만 롤백한다"고 설명하면 안 된다.

## 실보드 결과와 화면

[2026-11-05 최종 실보드 결과](docs/IVI_FIELD_MONITOR_RESULTS_2026-11-05.md)에 기록된
**1회 실행 측정값**이다. 반복 실험에 의한 일반화 결과는 아니다.

| 구간 | 처리 지연 p99 | 최대 끊김 | 누락 |
|---|---:|---:|---:|
| V3 설치 후 지도 닫힘 | 9.8ms | 113ms | 0% |
| V3 지도 열림·실시간 워커 부하 | 1854ms | 2012ms | 15% |
| IVI V2 롤백 후 | 17.2ms | 154ms | 0% |

해당 실행에서는 정차 중 롤백했고, V2를 약 62초 재검증해 `rolled_back`으로 종료했다.
실행 증적은 로컬 `.demo/ivi-e2e-final2-20261105/`와 `.demo/gate0-20261105/`에 있으며
로그·PCAP·캡처 등 `.demo/` 산출물은 Git에 포함하지 않는다.

Pi2의 [통합 콕핏 화면](dashboard/cockpit/Cockpit.qml)은 클러스터 캡처, IVI 화면,
A/B 슬롯, Pi3 판정, 지연과 이벤트 로그를 보여준다.
큰 지연 숫자는 Pi2의 빠른 로컬 버스 관측이고 **롤백 판정은 별도 Pi3 결과**를 사용한다.
응답 대기 중인 프레임이 길게 쌓이면 그 대기 시간의 하한도 표시값에 반영한다.
화면은 표시용이며 독립 검증기를 대체하지 않는다.

부하 실험·측정기 간섭·정차 판정·리셋 경쟁 상태 등의 실패와 수정 과정은
[실패 기록](docs/FAILURE_LOG_2026-11-05.md)에 남겼다.

## 코드 읽는 순서

| 파일 | 역할 |
|---|---|
| [dashboard/ivi/ivi.py](dashboard/ivi/ivi.py) | IVI 실행·지도 활성화·CPU 워커 |
| [dashboard/ivi/build.py](dashboard/ivi/build.py) | V2/V3 설정과 앱 payload 조립 |
| [dashboard/qt_cluster/main.cpp](dashboard/qt_cluster/main.cpp) | CAN 수신·Qt 모델 반영·0x300 응답 |
| [monitor/trace.py](capstone_ota/monitor/trace.py) | 프레임 해석·입력/응답 짝 맞추기 |
| [monitor/metrics.py](capstone_ota/monitor/metrics.py) | 창 지표·정상 기준선 |
| [monitor/detect.py](capstone_ota/monitor/detect.py) | 지연·끊김 규칙과 확정 조건 |
| [monitor/service.py](capstone_ota/monitor/service.py) | Pi3 실시간 감시·판정·프리즈 프레임 |
| [probation.py](capstone_ota/probation.py) | Pi2 시험 운영 감독·정차 대기 |
| [ivi.py](capstone_ota/ivi.py) / [device.py](capstone_ota/device.py) | IVI 서비스 연결·앱 A/B 상태·복구 |
| [relay.py](capstone_ota/relay.py) / [bundle.py](capstone_ota/bundle.py) | Central 중계·서명·해시 검사 |
| [can_verdict.py](capstone_ota/can_verdict.py) | Pi2↔Pi3 검증 판정을 CAN으로 인증 전달 |
| [debug_port.py](capstone_ota/debug_port.py) | 리콜 정비 포트 시나리오용 무인증 슬롯 쓰기 서비스 |
| [bench/load_phases.py](bench/load_phases.py) | 워커 개수·우선순위별 부하 측정 |
| [bench/drive_cycle.py](bench/drive_cycle.py) | 합성 주행 사이클 |
| [bench/cockpit_feeder.py](bench/cockpit_feeder.py) | 화면용 관측 데이터 수집 |

## 로컬 검증

Linux, Python 3.10+를 사용한다. 실제 Qt 통합 시험에는 C++17·Qt5 개발/런타임과 Xvfb가 필요하다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

# CPU 경합 감시·IVI·시험 운영 감독기 관련 시험
.venv/bin/python -m pytest -q tests/test_monitor_*.py tests/test_probation.py tests/test_ivi.py tests/test_tap.py

# 전체 시험: 실보드는 명시적으로 설정한 경우에만 실행
.venv/bin/python -m pytest -q
```

2026-11-11 기준 로컬 결과는 **274 passed (약 95초)**, `--hardware-config`를 준 실보드 결과는
**10 passed**다 (아래 "보안 시나리오" 절 참고). 로컬 실행은 고정 클러스터 C++/Qt 빌드와 IVI
공격 시험을 포함하며, `--hardware-config` 없는 실행은 실보드 전용 시험을 제외한다.
컴파일된 Qt 앱이 없으면 Qt 관련 시험도 skip되므로 Python 시험 통과만으로 Qt 검증을 주장하면 안 된다.
Qt 빌드와 기존 보드 설정은 [QT_CLUSTER.md](docs/QT_CLUSTER.md),
[THREE_BOARD_SETUP.md](docs/THREE_BOARD_SETUP.md)를 참고한다.
새 IVI·감시기의 보드 서비스 설정까지 자동 설치하는 단일 명령은 제공하지 않는다.
단일 클러스터 정리는 2026-11-11에 실보드로 배포·검증했다(10/10). 배포에는
`.demo/pi-qt-2026-11-11-fixed/capstone-qt-cluster-aarch64`(아래 절에서 설명하는
`--check` 레이스 수정이 들어간 재빌드)를 썼다. `cluster.json`을 요구하는 옛 바이너리는
새 고정 payload와 호환되지 않는다.

이전 `demo.qt_run`, `demo.qt_present`, `mixed` 표시 오류 시나리오와 그 전용 회귀 시험은 제거했다.
정상 CAN 수신·카운터·CRC·신선도·관찰자 신원 검사는 유지한다.
[과거 실보드 결과](docs/THREE_BOARD_RESULTS_2026-11-03.md),
[이전 패널 결과](docs/VISUAL_MONITOR_RESULTS_2026-11-04.md),
[초기 가상 시험](docs/VIRTUAL_COMPATIBILITY.md), [architecture.html](docs/architecture.html)은
변경 전의 역사적 기록이다. 삭제된 시나리오 설명·명령이 포함되어 현재 실행 안내로 사용하면 안 된다.
초기 MQTT 구현은 [legacy/](legacy/ARCHIVE.md)에 보존되어 있다.

## 보안 시나리오 세 가지

A/B 복구 경로를 겨냥한 공격을 세 단계로 쌓아서 검증했다. 각각 독립적으로 테스트하며,
아래 순서로 읽으면 "일반 공격 → 포트가 뚫려 있을 때의 공격 → 그 판정 자체를 어떻게 더
단단하게 전달하는가" 흐름을 따라갈 수 있다.

### 1) 복구 슬롯 바꿔치기 공격 시험 (일반)

IVI V2를 정상 설치하고 V3를 시험 설치한 뒤, 시험 코드가 복구 슬롯을 **정식 서명된 IVI V1**으로
바꾼다. 신뢰 상태 기록은 그대로 유지한다. 시험용 실패 판정으로 복구를 요청하면 기록된 V2 앱 트리
해시와 달라 V1 실행 전에 `recovery_failed`로 차단한다.

```bash
.venv/bin/python -m demo.ivi_attack run --output .demo/ivi-security-new
.venv/bin/python -m pytest -q tests/test_ivi_attack.py
```

이 시험은 IVI의 실제 초기화·서명·해시·A/B·복구 검사를 실행하며 클러스터를 만들거나 교체하지 않는다.
**롤백 계기는 명시적인 시험용 판정**이며 CPU 이상을 실제 측정한 결과가 아니다. 정상 대조군도
초기화 경로만 검사하며 정상 CPU 호환성을 입증하지 않는다. 실제 침입·V1 취약점 악용은 아니며
V1 차단 뒤 자동 서비스 복원은 없다. [실보드 기반 시험](tests/hardware/test_physical_can.py)의
기존 OTA/공격 항목은 고정 클러스터 바이너리를 이용하는 전송·보안 인프라 회귀 시험이며, 현재 IVI
CPU 시나리오의 실보드 자동화로 해석하지 않는다.

### 2) 디버그 포트를 이용한 복구 슬롯 바꿔치기 (리콜 정비 포트 시나리오)

리콜 정비 때 열어 둔 진단/정비용 포트가 그대로 열려 있다고 가정한다. 이 포트는 복구 슬롯
디렉터리 하나의 내용만 바꿀 수 있고(`capstone_ota/debug_port.py`), `state.json`·`trusted.pub`·
`current`·`.lock`은 코드 구조상 건드릴 수 없다. IVI V2가 정상 커밋된 뒤 V3가 시험 중일 때
이 포트로 복구 슬롯(V2)을 다른 정식 서명 릴리스로 바꾼다.

CPU 경합으로 외부 검증(Pi3 역할)이 실패를 보고하면 복구 절차가 시작되는데, **복구 슬롯을 실제로
실행하기 직전**에 `Device._verify_record`가 그 슬롯의 해시를 커밋 시점에 기록해 둔 값과
**정확히 일치하는지** 비교한다. "서명이 유효한가"가 아니라 "우리가 커밋했던 바로 그것인가"를 보기
때문에, 바꿔친 것이 다른 정식 서명 릴리스여도 막힌다.

이 재검증에는 데모 전용 끔 스위치(`Device(..., recovery_verification=False)`)가 있다. CLI
(`capstone-ota cluster-update`/`ivi-update`)와 `forward`의 원격 대상 JSON 스키마 어디에도
이 값을 노출하는 통로가 없어 네트워크·설정 파일로는 절대 켜지지 않으며, 끄면 바로 같은 공격이
성공하는 대조군 시연에만 쓴다.

```bash
.venv/bin/python -m pytest -q tests/test_debug_port.py tests/test_debug_port_rollback_scenario.py
```

소프트웨어 시험(위 명령)은 가짜 Pi3 검증기 프로세스가 V3 시험 평가 도중 실제로 디버그 포트에
접속해 복구 슬롯을 바꾸고, 방어 모드에서는 복구가 거부되며(`recovery_failed`, 재검증 사유:
tree hash mismatch) 취약 모드에서는 바꿔친 이미지가 실제로 활성화되는 것까지 양쪽 다 확인한다.

실보드(Pi1/Pi2/Pi3) 시험은 `bench/physical.py: PhysicalBench.debug_port_rollback()`과
[`tests/hardware/test_physical_can.py`](tests/hardware/test_physical_can.py)의
`test_SEC_005_...`로 작성돼 있고, **실제로 세 보드에서 돌려서 통과했다**:

```bash
.venv/bin/python -m pytest tests/hardware/test_physical_can.py \
  --hardware-config .demo/three-board-config-fixed.json
# 10 passed
```

이 실행에서 이 기능과 무관한 기존 버그 하나를 찾아 고쳤다: `dashboard/qt_cluster/main.cpp`의
`--check`가 `engine.rootObjects().isEmpty()`를 `engine.load()` 직후 딱 한 번만 동기적으로
검사해서, 실제 보드의 offscreen/software 렌더링에서는 QML 루트 오브젝트가 아직 준비되기 전에
검사해버려 **항상** 실패하는 경쟁 상태(race condition)였다. 이 때문에 `compatible`/`attack`
시나리오를 포함한 기존 실보드 시험 전체가 (이 변경과 무관하게) 막혀 있었다. `gdb`로 Pi2에서
직접 디버그 빌드를 떠서 원인을 확인했고, `QCoreApplication::processEvents()`를 최대 500ms
돌리는 재시도로 고쳤다. 함께 발견한 것: 아이폰 핫스팟 시절 IP가 박혀 있던 SSH 별칭
(`capstone-verifier`, `capstone-source`) 두 개. 전부
[`.planning/2026-11-11-debug-port-rollback-defense/progress.md`](.planning/2026-11-11-debug-port-rollback-defense/progress.md)에
시행착오 그대로 기록했다.

> **중요한 한계:** 이 실보드 시험의 V3 실패는 `scenario('attack')`과 같은 방식으로
> **결정적으로 강제**한 것이며, 실제 Pi3의 `Probation`/`monitor.detect` p99 CPU 경합 판정이
> 트리거한 것은 아니다 (그 실시간 CAN 감시 서비스는 아직 Pi3에 상시 서비스로 올라가 있지 않음).
> `bench/load_phases.py`로 실제 CPU 부하는 함께 돌지만 판정에는 아직 반영되지 않는다. 이 부분을
> `PhysicalBench`에 제대로 연결하는 건 별도 후속 작업으로 남겨 둔다. ("관리자 권한으로 신뢰
> 기록까지 변조하는 공격은 범위 밖"이라는 아래 원칙과 같은 경계를, 복구 직전 재검증이라는 구체적인
> 메커니즘으로 보여 준다.)

### 3) 검증 결과(pass/fail) 전달 경로를 와이파이에서 CAN 버스로 (Pi2 ↔ Pi3)

위 디버그 포트 방어와는 별도의 질문: "3보드 검증 구조에서 Pi2가 Pi3한테 '통과했어?'를
SSH(와이파이)로 물어보고 그 대답을 그대로 믿는 게 맞나?"에 대한 답으로 추가한 기능이다.

**문제**: `ClusterDevice._activate()`가 업데이트 시험/복구 때마다 `verifier_command`로 SSH를
실행해 Pi3의 검증 결과(JSON의 `passed` 필드)를 그대로 신뢰했다. 이 경로가 살아있는 동안은:
- 와이파이를 방해(jamming)하면 업데이트 확인이 전부 실패로 떨어져 업데이트를 영구히 막을 수 있음
- SSH 세션이 뚫리면(또는 중간에서 응답이 조작되면) `passed: true`를 위조해 바꿔친 복구본을
  그대로 통과시킬 수 있음 — 디버그 포트 방어가 막는 것과 다른 종류의 공격 표면

**바꾼 것**: SSH는 이제 "Pi3야, 지금 확인해봐"라는 트리거로만 쓰고(이게 막혀도 그냥 확인이
안 일어날 뿐, 안전한 쪽으로 실패), **실제로 Device가 믿는 pass/fail 판정은 물리 CAN 버스로
받는다**(`capstone_ota/can_verdict.py`). Pi2가 매번 새로 임의의 nonce를 만들어 CAN 소켓을
먼저 열어 듣기 시작한 뒤에 SSH 트리거를 보내고(순서가 바뀌면 신호를 놓침 — CAN은 방송이라
듣고 있지 않으면 그 순간 신호가 사라짐), Pi3는 자기 판정을 `HMAC-SHA256(사전 공유 비밀키,
nonce ∥ 결과)`로 서명해 CAN 프레임(`0x7F1`)에 실어 보낸다. Pi2는 nonce와 서명이 모두
맞아야만 그 판정을 받아들이고, 그 외(타임아웃, 다른 nonce, 잘못된 서명)는 전부 "확인 못
했음"으로 처리해 실패 쪽으로 간다.

이 설계가 지키는 것: 와이파이를 방해하거나 SSH를 조작해도 — 비밀키를 모르면 — **가짜
PASS를 만들 수 없다.** 비밀키는 두 보드 바깥(배포 시점)에만 존재하며 어느 네트워크 경로로도
노출되지 않는다.

```bash
.venv/bin/python -m pytest -q tests/test_can_verdict.py tests/test_cluster_can_verdict.py
```

소프트웨어 시험(노트북에서 `vcan0` 가상 CAN 인터페이스로): 정상 왕복, "SSH는 `passed:true`라고
위조했지만 CAN에는 아무것도 안 보냄" 공격 차단, "CAN에 신호는 보냈지만 비밀키가 틀림" 공격
차단까지 8개 테스트로 확인(274개 전체 스위트 통과). **아직 실보드(Pi2/Pi3)에는 배포하지
않았다** — `qt-verifier-peers.example.json`에 `can_channel`/`can_secret_file` 필드를 추가해
다음에 실제 보드에 올릴 때 쓸 수 있게만 해 뒀다.

## 현재 한계와 남은 일

- 부하는 실시간 우선순위 반복 계산을 이용한 인위적 시험이다. 실제 IVI의 부하 특성은 검증하지 않았다.
- 주행 입력은 합성 데이터이며 일주일 사용·실차 내구·전원 차단 복구 시험을 대신하지 않는다.
- 최종 실보드 실행에는 계측기 간섭을 줄이기 위한 코어 배치를 적용했다. 배치가 다른 환경의 결과를 보장하지 않는다.
- 정차 대기 시간 초과 폴백과 Pi3 관측 상실 시의 축소 판단이 있어 엄격한 정차 보장은 없다.
- Pi3 감시기는 재부팅 후 자동 시작 설정이 없다. listen-only 하드웨어 모드도 미설정이다.
- 서명된 자원 계약·규칙 완화 금지 병합·설치 전 정적 자원 호환성 검사는 설계만 있고 미구현이다.
- 최종 표시 수정 이후 라이브 화면 재확인은 하지 못했다. 보드 전원·열 제한 이력도 결과 문서에 기록했다.
- OS OTA, 독립 광학 검증, 관리자 권한으로 신뢰 기록까지 변조하는 공격 방어는 범위 밖이다.
- 디버그 포트 복구 슬롯 바꿔치기의 실보드 V3 실패는 아직 결정적 강제이며, 실제 Pi3 CPU 경합
  판정이 트리거한 게 아니다 (자세한 내용은 위 "보안 시나리오" 2번 참고).
- Pi2-Pi3 검증 결과를 CAN 버스로 인증해 전달하는 기능은 소프트웨어 시험(`vcan0`)으로만
  확인했다. 실보드 배포·실측은 아직 안 했다(사전 공유 비밀키를 두 보드에 안전하게 배치하는
  절차부터 필요).

HTTP 전송은 실험망 용도이며 암호화·접속 인증을 제공하지 않는다.
개인키·SSH 키·Wi-Fi 비밀번호를 Git이나 릴리스 제공 폴더에 넣지 않는다.
Volvo 원본 Qt 계기판 출처와 MIT 라이선스는 [UPSTREAM.md](dashboard/qt_cluster/UPSTREAM.md)에 있다.
