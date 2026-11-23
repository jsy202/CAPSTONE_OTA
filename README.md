# CAPSTONE_OTA

**다중 ECU 환경에서의 OTA 소프트웨어 호환성 검증 및 자동 롤백 시스템**

[![tests](https://github.com/jsy202/CAPSTONE_OTA/actions/workflows/tests.yml/badge.svg)](https://github.com/jsy202/CAPSTONE_OTA/actions/workflows/tests.yml)

차량용 소프트웨어를 무선으로 갱신(OTA)했을 때, 새 앱이 **실행은 되는데 같은 보드의 다른 기능을 느리게 만드는**
문제를 잡아내고 이전 버전으로 되돌리는 Raspberry Pi 3대짜리 프로젝트다.

- 갱신한 IVI(인포테인먼트) 앱이 CPU를 독점해 같은 보드의 **디지털 클러스터** 응답이 늦어지는 상황을,
  **별도 보드(Pi3)** 가 CAN 버스에서 직접 재서 감지하고 **자동으로 이전 버전으로 롤백**한다.
- 롤백 경로 자체를 노린 공격(복구 슬롯 바꿔치기)을 막고, 막은 뒤에는 **사람이 관제 서버에서 승인해야만** 복원한다.

> 학생 캡스톤 프로젝트다. 실차·양산 시스템이 아니며, 특정 제조사의 결함을 재현했다고 주장하지 않는다.
> 무엇을 실제 보드에서 확인했고 무엇을 하지 않았는지는 [한계](#한계)에 모두 적었다.

## 목차

1. [무엇을 해결하나](#무엇을-해결하나)
2. [구조](#구조)
3. [시나리오 1: IVI의 CPU 경합과 자동 롤백](#시나리오-1-ivi의-cpu-경합과-자동-롤백)
4. [시나리오 2: 롤백 경로 공격과 사람 승인 복원](#시나리오-2-롤백-경로-공격과-사람-승인-복원)
5. [실보드 결과](#실보드-결과)
6. [직접 실행해 보기](#직접-실행해-보기)
7. [코드 지도](#코드-지도)
8. [문서](#문서)
9. [한계](#한계)

## 무엇을 해결하나

업데이트한 앱이 설치되고 실행된다고 해서 기존 기능과 함께 정상 동작한다는 뜻은 아니다.
같은 컴퓨팅 자원을 나눠 쓰면 새 기능의 CPU 사용과 실행 우선순위가 기존 기능의 응답 시간을 깨뜨릴 수 있다.
이 프로젝트는 설치 직후의 실행 확인에 더해, **시험 운영 기간 동안 기존 클러스터의 처리 지연·끊김을 감시**하고
문제가 지속되면 업데이트한 IVI를 되돌린다.

Pi2의 IVI와 클러스터는 **한 Linux 보드에서 도는 별개의 앱**이다. 물리적으로 분리된 두 ECU가 아니라
통합 콕핏의 공유 자원 환경을 흉내 낸 것이다.

## 구조

```text
노트북: 서명된 앱 릴리스 제공 · 관제 서버(승인 화면)
  │ LAN
  ▼
Pi1 Central ── 검증·중계 · 진단 게이트웨이 ──▶ Pi2 Cockpit
  │                                            ├─ 디지털 클러스터: CAN 수신 → Qt 모델 → 0x300 응답
  │                                            └─ IVI: 앱 A/B 시험 설치 → 시험 운영 → 확정 또는 롤백
  │ CAN 0x200 차량 입력                                 ▲
  └──────────────────── CAN 버스 ──────────────────────┤
                              │                        │ 판정 조회(SSH)
                              ▼                        │
                       Pi3 Verifier ───────────────────┘
                       0x200·0x300 직접 수신 → 지연·끊김 판정
```

- Raspberry Pi 3대, MCP2515 HAT, `can0` 500kbit/s. 파일은 LAN으로, 차량 입력과 처리 응답은 CAN으로 오간다.
- **갱신·롤백 대상은 IVI**다. 클러스터는 이 시나리오에서 교체하지 않는다.
- **A/B는 앱 디렉터리 두 개**다. OS 파티션·커널·부트로더 OTA가 아니다.
- 릴리스는 `release.json`, `release.sig`, `app.tar.gz`로 구성한다. Ed25519 서명, 파일 해시, 복구 앱 트리 해시를 검사한다.
- Pi1은 릴리스 중계 외에 **진단 게이트웨이**도 맡는다. 정비 포트로 가는 요청은 Pi1을 거치고 기록된다.

## 시나리오 1: IVI의 CPU 경합과 자동 롤백

1. **V2:** 지도가 없는 IVI. 클러스터가 CAN 입력을 정상 처리한다.
2. **V3 설치:** 지도 기능이 추가된다. 지도가 닫혀 있으면 부하가 크지 않다.
3. **지도 열기:** V3가 CPU 계산 워커를 코어 수만큼 실행한다(Pi4는 4개, Linux `SCHED_RR` 우선순위 10).
4. **기존 기능 저하:** 클러스터가 CPU 실행 기회를 늦게 받아 입력 반영이 밀린다.
5. **Pi3 감지:** 지연·끊김이 지속되면 이상을 확정한다.
6. **IVI 롤백:** Pi2 감독기가 **정차**를 확인한 뒤 V2로 복구하고, 다시 감시해 정상화를 확인한다.

### 무엇을 어떻게 재나

- Pi1의 `0x200`(속도·RPM·기어·카운터·CRC)과 Pi2의 `0x300`(Qt 모델 반영 직후의 속도·카운터·CRC)을
  Pi3가 **자기 CAN 소켓에서 직접** 받아, 같은 카운터의 입력과 응답을 짝지어 커널 수신 시각 차이로 지연을 계산한다.
  보드 사이 시계 동기화나 SSH 왕복 시간은 쓰지 않는다.
- 판정 창은 10초, 이동 간격 5초. **창 p99 지연 ≤ 100ms**를 어긴 상태가 같은 유형으로 3개 창 연속이면 확정한다.
  (100ms는 CAN 입력 한 주기 안에 처리한다는 개발 기준이며 업계 표준이 아니다.)
- 보조로 정상 기준선 대비 p99, CUSUM, 드리프트, 응답 끊김·누락, 입력 주기 이상도 검사하며, 이들은 2개 이상 동시 발화와
  지속 조건을 요구한다. 같은 지연 데이터에서 계산한 통계라 독립된 증거는 아니다.
- 측정 대상은 **클러스터 소프트웨어의 처리 응답 지연**이다. 화면 픽셀이 그려진 시간이 아니고, 악성 클러스터의
  허위 응답 방어는 구현하지 않았다.

**부하의 정체:** 직접 만든 Python 다중 프로세스 반복 계산이다. 실제 지도 타일 디코딩을 측정해 재현한 것이 아니며,
지도 렌더링 자체가 지연 원인이라는 주장도 아니다. 일반 우선순위 부하에서는 문제가 재현되지 않아 전 코어 실시간 부하를 택했다.

## 시나리오 2: 롤백 경로 공격과 사람 승인 복원

리콜 정비용 진단 포트가 열려 있다고 가정하고, 공격자가 **복구 슬롯을 다른 정식 서명 릴리스(V1)로 바꿔치기**한다.

1. **차단:** 롤백 직전에 Pi2가 복구 슬롯의 트리 해시를 *커밋 시점에 기록한 값*과 비교한다. "서명이 유효한가"가 아니라
   "우리가 커밋했던 바로 그것인가"를 보므로, 바꿔친 것이 정식 서명 릴리스여도 `recovery_failed`로 막힌다.
   (이미지가 써지는 것 자체를 막는 것은 아니다.)
2. **격리·보고:** Pi1 게이트웨이가 게이트를 닫고 관제 서버에 사고를 보고한다. 계기판에는
   `서버 승인 대기 중 → 승인/거절` 상태가 표시된다.
3. **사람 승인:** 관제 서버 화면에서 사람이 승인하면 **서명된 복원 지시서**가 발급된다.
   지시서는 차량·nonce·만료에 묶여 1회만 쓸 수 있다.
4. **복원:** Pi1이 서버에서 V2 원본을 받아 Pi2에 설치한다. 커밋 기록과 정확히 같은 릴리스·트리 해시만 받는다.
   이후 같은 공격은 `ERROR gateway closed`로 거부된다.

그 밖에, Pi2→Pi3 검증 결과(pass/fail)를 SSH 응답이 아닌 **HMAC으로 서명한 CAN 프레임**으로 받는 기능이 있다
(와이파이 방해·SSH 응답 위조로 가짜 PASS를 만들 수 없게 하기 위함). 이 기능은 가상 CAN(`vcan0`) 시험으로만 확인했다.

자세한 흐름과 방어 범위는 [GATEWAY_INCIDENT_RESPONSE.md](docs/GATEWAY_INCIDENT_RESPONSE.md)에 있다.

## 실보드 결과

[2026-11-05 최종 실보드 결과](docs/IVI_FIELD_MONITOR_RESULTS_2026-11-05.md)의 **1회 실행 측정값**이다(반복 실험 결과 아님).

| 구간 | 처리 지연 p99 | 최대 끊김 | 누락 |
|---|---:|---:|---:|
| V3 설치 후 지도 닫힘 | 9.8ms | 113ms | 0% |
| V3 지도 열림·실시간 워커 부하 | 1854ms | 2012ms | 15% |
| IVI V2 롤백 후 | 17.2ms | 154ms | 0% |

정차 중 롤백했고, V2를 약 62초 재검증해 `rolled_back`으로 끝났다.

시나리오 2는 2026-11-19에 실제 보드 3대에서 실행했다. 노트북 → Pi1 → 이더넷 → Pi2 경로로 공격이 도달하고,
Pi2의 정비 포트는 노트북에서 직접 닿지 않으며, 승인 후 V2가 복원되고 재공격이 거부되는 것까지 확인했다.
처음 실행은 V3 실패 판정을 강제했고, 이후 **실제 Pi3 감지**와 **사람의 승인 클릭**을 각각 1회씩 확인했다.
(사람 승인에서 클릭한 사람은 프로젝트 작성자다.)

## 직접 실행해 보기

Linux, Python 3.10+. 보드 없이 시나리오 흐름을 볼 수 있다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,cockpit]'

# 전체 시험 (실보드 시험은 --hardware-config를 줄 때만 실행됨)
.venv/bin/python -m pytest -q

# 시나리오 2를 노트북 한 대에서: 출력된 주소를 열어 승인 버튼을 누른다
.venv/bin/python -m demo.incident_drill run --output .demo/incident-drill

# 계기판 화면 흐름만 합성 데이터로 보기 (실제 OTA·CAN·CPU 부하는 실행하지 않음)
.venv/bin/python -m demo.local_cockpit_replay
```

- 최근 전체 시험 결과: **580개 통과, 35개 건너뜀 (2026-11-23, 약 3분)**. 건너뛴 시험은 실보드 전용, 컴파일된 Qt 앱이 필요한 것, `vcan0`가 필요한 것이다.
  CI는 Qt·vcan 시험이 건너뛰어지지 않도록 감시한다. 건너뛴 시험은 실행되지 않았으니 Python 시험 통과만으로 Qt 검증을 주장하지 않는다.
- 실보드 준비와 점검은 `python -m bench.preflight CONFIG`(읽기 전용)로 한다. 보드 설정은 [PI_SETUP.md](docs/PI_SETUP.md),
  [THREE_BOARD_SETUP.md](docs/THREE_BOARD_SETUP.md), Qt 빌드는 [QT_CLUSTER.md](docs/QT_CLUSTER.md)를 본다.
- CI(정적 검사·시험·Qt 빌드·서명 릴리스 생성)는 [CI_CD.md](docs/CI_CD.md)에 정리했다. 실보드 검증은 자동화에 포함하지 않았다.

## 코드 지도

| 위치 | 역할 |
|---|---|
| [dashboard/ivi/](dashboard/ivi/) | IVI 앱: 실행·지도 활성화·CPU 워커, V2/V3 조립 |
| [dashboard/qt_cluster/](dashboard/qt_cluster/) | 클러스터(Qt/QML): CAN 수신·모델 반영·0x300 응답 |
| [dashboard/cockpit/](dashboard/cockpit/) | Pi2 통합 모니터링 화면(Cockpit.qml) |
| [capstone_ota/monitor/](capstone_ota/monitor/) | Pi3 감시기: 프레임 짝 맞추기·창 지표·판정·프리즈 프레임 |
| [capstone_ota/probation.py](capstone_ota/probation.py) | Pi2 시험 운영 감독·정차 대기 |
| [capstone_ota/device.py](capstone_ota/device.py), [ivi.py](capstone_ota/ivi.py) | 앱 A/B 상태·복구·트리 해시 검사 |
| [capstone_ota/relay.py](capstone_ota/relay.py), [bundle.py](capstone_ota/bundle.py) | Central 중계·서명·해시 검사 |
| [capstone_ota/gateway.py](capstone_ota/gateway.py), [incident_responder.py](capstone_ota/incident_responder.py) | Pi1 진단 게이트웨이·사고 보고·승인 후 복원 |
| [capstone_ota/incident_server.py](capstone_ota/incident_server.py), [order.py](capstone_ota/order.py) | 관제 서버(승인 화면)·서명된 복원 지시서 |
| [capstone_ota/can_verdict.py](capstone_ota/can_verdict.py) | Pi2↔Pi3 판정을 CAN으로 인증 전달 |
| [capstone_ota/debug_port.py](capstone_ota/debug_port.py) | 정비 포트 시나리오용 **무인증** 슬롯 쓰기 서비스(공격 시험용) |
| [bench/](bench/) | 실보드 시험·부하 측정·사전 점검(`preflight.py`)·시연 오케스트레이션(`incident_field.py`) |
| [demo/](demo/) | 노트북 단독 시연(사고 대응 드릴, 화면 재생) |
| [tests/](tests/) | 단위·통합·실보드 시험 |
| [legacy/](legacy/ARCHIVE.md) | 초기 MQTT 구현(보존용) |

## 문서

- **설계·결과:** [GATEWAY_INCIDENT_RESPONSE.md](docs/GATEWAY_INCIDENT_RESPONSE.md), [IVI_FIELD_MONITOR_RESULTS_2026-11-05.md](docs/IVI_FIELD_MONITOR_RESULTS_2026-11-05.md),
  [VERIFICATION_RUNBOOK.md](docs/VERIFICATION_RUNBOOK.md), [TEST_CASES.md](docs/TEST_CASES.md), [OS_LEVEL_AB_DESIGN.md](docs/OS_LEVEL_AB_DESIGN.md)
- **시행착오 기록(실패와 수정 과정):** [11-05](docs/FAILURE_LOG_2026-11-05.md), [11-12](docs/FAILURE_LOG_2026-11-12.md),
  [11-17](docs/FAILURE_LOG_2026-11-17.md), [11-19](docs/FAILURE_LOG_2026-11-19.md)
- **역사적 기록(삭제된 시나리오 설명이 섞여 있어 현재 실행 안내로 쓰면 안 됨):**
  [THREE_BOARD_RESULTS_2026-11-03.md](docs/THREE_BOARD_RESULTS_2026-11-03.md), [VISUAL_MONITOR_RESULTS_2026-11-04.md](docs/VISUAL_MONITOR_RESULTS_2026-11-04.md),
  [VIRTUAL_COMPATIBILITY.md](docs/VIRTUAL_COMPATIBILITY.md), [architecture.html](docs/architecture.html)

## 한계

- 부하는 실시간 우선순위 반복 계산을 쓴 **인위적 시험**이다. 실제 IVI의 부하 특성은 검증하지 않았다.
- 주행 입력은 합성 데이터이며 장시간 사용·실차 내구·전원 차단 복구 시험을 대신하지 않는다.
- 정차 대기가 `max_wait_s`(기본 600초)를 넘거나 Pi3 관측이 끊기면 축소된 판단으로 롤백한다. **"항상 정차 후에만 롤백한다"고 말할 수 없다.**
- Pi3 감시기는 재부팅 후 자동 시작 설정과 listen-only 하드웨어 모드가 없다.
- 서명된 자원 계약, 설치 전 정적 자원 호환성 검사는 설계만 있고 구현하지 않았다.
- 시나리오 2의 실보드 실행은 각 1회이며 반복 측정이 아니다. 관제 서버가 꺼진 경우(`maintenance_required`)는 실보드에서 실행하지 않았다.
  다운그레이드 방어 자체(부팅 전 검사, 기준값의 외부 보관)는 강화하지 않았다.
- CAN 인증 판정 전달은 `vcan0` 소프트웨어 시험만 했고 실보드 배포는 하지 않았다.
- 보드 전원·열 제한 이력이 결과 문서에 기록돼 있으며, 결과는 그 환경에서의 값이다.
- OS OTA, 독립 광학 검증, 관리자 권한으로 신뢰 기록까지 변조하는 공격 방어는 범위 밖이다.

HTTP 전송은 실험망 용도이며 암호화·접속 인증을 제공하지 않는다. 개인키·SSH 키·Wi-Fi 비밀번호를 Git이나 릴리스 제공 폴더에 넣지 않는다.

클러스터 화면은 자체 제작(`dashboard/qt_cluster/Cluster.qml`)이다. 글꼴 DM Sans는 SIL OFL
([`dashboard/qt_cluster/fonts/dmsans-OFL.txt`](dashboard/qt_cluster/fonts/dmsans-OFL.txt)).
지도 데이터: © OpenStreetMap contributors (ODbL), Overpass API로 받은 서울시청 일대 도로망.
