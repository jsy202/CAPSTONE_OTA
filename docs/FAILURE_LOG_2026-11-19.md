# 시행착오와 검증 기록 — 2026-11-19 (게이트웨이 사고 대응의 실보드 실행)

범위: 11-17에 로컬 프로세스로만 시험한 사고 대응(2-1)을 실제 보드 3대에서 실행하고, 로컬에서 "개발 편의로 가짜였던" 부분을 줄였다.
처음 두 실행은 V3 실패 판정이 강제였고, 같은 날 뒤에 **실제 Pi3 감지 모드(`--verdict real`)**를 추가해 다시 실행했다(아래 "실제 감지" 절). 처음 실행들은 자동 승인으로 시험했고, 사람이 승인을 누르는 실행은 같은 날 뒤에 했다(아래 "화면·사람 승인 실행 (이어서)" 절).

| 관찰한 문제 | 확인한 원인 | 조치 및 확인 | 상태 |
|---|---|---|---|
| 세션 재개 후 `git fetch`가 `object file … is empty`로 실패 | 컴퓨터 비정상 종료로 merge 커밋 객체가 0바이트, PR #8로 바뀐 작업 폴더 파일 11개도 0바이트 | 빈 객체만 삭제 후 재fetch, 11개 파일은 HEAD에서 복원(`git restore --source=HEAD`). `fsck` 정상, 관련 시험 49개 통과. 잃은 작업 없음(내용은 GitHub에 있었음) | 복구 |
| Pi1이 Pi2에 접속할 SSH 키가 없음 | Pi1의 `~/.ssh`에 `authorized_keys`만 있었음 (이전 시험은 노트북이 직접 접속) | Pi1 전용 키를 만들고 Pi2에 `from=`, `restrict`, **고정 명령 게이트**로 등록. 임의 명령·셸 시도는 `refused`(126) | 보강 |
| 처음 만든 게이트가 `repair` 하나만 허용 | Pi1은 갱신(V3 설치)도 Pi2에 보내야 함 | `repair_gate`를 `ota_gate`로 일반화: 갱신·복원 두 가지를 정확히 일치할 때만 허용, 갱신은 고정된 검증 키·명령만. 변형 29개 시험 | 수정 |
| Pi2의 시스템 `cryptography` 38.0.4 (< 요구 41) | 보드 OS 패키지가 오래됨 | 시스템 패키지를 쓰지 않고 보드마다 별도 venv(46.x)에 설치. 기존 시험 환경은 건드리지 않음 | 우회 |
| 복원용 V2 원본이 노트북에 없는 줄 알았음 | 옛 작업 폴더(`.worktrees/…/.demo/ivi-demo4`)에 있었음. 복원은 커밋 기록과 **바이트 단위로 같은 릴리스**(sha256 `432419db…`)만 받음 | sha256 일치를 확인하고 그 릴리스와 서명 키를 사용. 일치하지 않았다면 기준선을 새로 만들어야 했음 | 확인 |
| 첫 실행이 `status` 호출에서 예외 | `status`는 결과가 `rolled_back`이면 종료 코드 2를 돌려줌(유효한 안정 상태). 종료 코드를 오류로 취급함 | 종료 코드가 아니라 출력(JSON)을 파싱 | 수정 |
| **복원이 실패 (`actor exited unsuccessfully`), Pi2가 `recovery_failed`로 남음** | 응답기가 복원 명령에 **갱신의 검증 명령**(V3 시험, 25초 강제 실패)을 재사용. 게이트의 복원 고정값(통과 명령)과 달라 거부. 갱신의 검증과 복원 후 재검증은 본래 다른 명령 | 대상 설정에 `repair_check_command` 분리. 재현 시험(RED)을 먼저 쓰고 수정(GREEN). 이 결함은 **로컬 시험이 아닌 실보드에서만 드러남** | 수정 |
| 실패한 실행이 Pi2 디바이스를 `recovery_failed`로 남김 | 위 결함의 결과. 이 상태에서는 기준선 검사가 실행을 막음 | 노트북에서 V2 원본을 서빙해 `ivi-repair`를 직접 실행 → 실제 보드에서 `repair()`가 동작, `stable`/V2/슬롯 A | 복구 |
| 게이트웨이 로그에 `invalid literal for int()` 오류 두 줄 | 오케스트레이터의 포트 확인(`connect` 후 바로 닫음)이 빈 요청으로 기록됨 | 동작 문제는 아님. 로그 해석 시 포트 확인 요청으로 구분(결과 해석에서 제외) | 기록 |
| 조사 중 `cd`로 작업 폴더가 옛 워크트리로 바뀜 | 읽기 전용 확인 명령이 셸의 현재 폴더를 이동시킴 | 이후 명령은 저장소 절대경로로 실행 | 주의 |

## 결과 (두 번째 실행, 자동 승인, 강제 판정)

```
baseline        V2 stable (slot A)
exposure        laptop->Pi1:7400 = reachable, laptop->Pi2:7312 = NOT reachable, Pi2 listens on 10.77.0.2:7312 only
v3_in_trial     seen
attack          response=OK  (source 172.20.10.14 recorded by the gateway)
incident        INC-0001 reported -> approved -> closed (outcome rolled_back)
reattack        ERROR gateway closed
final           V2 stable slot A, rolled_back
```

두 번째 실행 전까지 첫 실행은 복원에서 실패했고(위 표), 그 결함을 고친 뒤 통과했다. 한 번의 실행 결과이며 반복 측정이 아니다.

## 실제 Pi3 감지로 교체 (`--verdict real`)

강제 판정 대신 보드 3대가 실제로 움직인다: Pi1이 CAN 0x200 주행 프로파일(정차 포함)을 보내고, Pi2에서 Qt 클러스터(코어 0 고정)와 IVI V2가 돌고,
Pi3 감시기가 baseline으로 지연을 보다가, V3 시험 중 지도 플래그가 켜지면 RT 부하가 걸려 Pi2 probation이 Pi3 판정을 읽는다.
정차 구간에서 판정이 실패면 롤백이 아니라 복구 거부 → 사고 흐름으로 이어진다.

| 관찰한 문제 | 확인한 원인 | 조치 및 확인 | 상태 |
|---|---|---|---|
| 실행이 `ssh` 종료 코드 255로 즉시 죽음 | `pkill -f <패턴>`을 원격 셸 한 줄에 넣으면 그 셸 자신의 명령줄이 패턴과 일치해 스스로를 죽임 | 정리 호출을 별도 ssh 호출로 분리 | 수정 |
| `cd … && nohup … &`가 있는 ssh가 끝나지 않음 | 백그라운드 프로세스가 ssh 채널의 표준 출력을 잡고 있음 | `PYTHONPATH=… nohup … >/dev/null 2>&1 &` 형태로 입출력을 모두 끊음 | 수정 |
| 감시기 상태 확인이 계속 실패 | 기대한 상태 이름이 실제 값(`observing`)과 달랐음 | 실제 감시기 출력 기준으로 수정 | 수정 |
| 지난 실행의 증적 묶음 생성 실패 | 증적 디렉터리가 없을 때 tar가 실패 | 디렉터리 없음을 허용 | 수정 |
| **복구가 거부된 뒤에도 실패한 V3가 계속 실행됨** | 코드 추적으로 발견(`recovery_failed` 진입 시 시험 서비스를 멈추는 경로가 없음). 변조된 슬롯의 V3가 도는 채로 사고 대기 | 실패한 복구에서 서비스 정지(`service_stopped_after_recovery_failure`), 재현 시험 먼저. **실보드에서 이 이벤트가 실제로 기록됨** | 수정 |
| **실행 중 사용자가 보드 전원을 잘못 건드려 재부팅** | V3 시험 중, 변조된 슬롯 A가 활성인 채로 전원이 끊김 | 재부팅 뒤 복구가 트리 해시 불일치를 잡고 **V1을 실행하지 않았으며 서비스도 시작하지 않음**. 이후 V2로 `repair`. 의도하지 않은 실험이지만 "전원 차단+변조" 경로가 보드에서 안전함을 보였다 | 확인 |
| 내 판단 오류: 중단된 명령이 "실행되지 않았다"고 보고 | 보고 전에 증적을 확인하지 않음. 이후 커밋·V3 이벤트·게이트웨이 로그의 공격 `OK`로 이미 시작됐음이 확인됨 | 사용자에게 정정. 중단된 명령의 실행 여부는 로그로 확인한 뒤에만 말한다 | 정정 |
| 재시도가 Pi3 `pkill` 단계에서 시간 초과 | 노트북이 iPhone 핫스팟에서 학교 Wi-Fi로 옮겨져 보드 세 대에 모두 접속 불가 | 노트북을 핫스팟에 재연결(보드 IP 동일 확인). 잔여 서비스를 읽기 전용으로 점검해 없음을 확인한 뒤 재실행 | 해결 |
| `nmcli connection up iPhone`이 실패하며 기존 Wi-Fi도 끊김 | 핫스팟 SSID가 보이지 않을 때 연결 시도가 현재 연결을 먼저 내림 | `nmcli connection up KMU_WiFi`로 복구. 이후 SSID가 보이는 것을 확인한 뒤 전환 | 주의 |

### 결과 (실제 감지, 자동 승인, 1회 실행)

```
forced_verdict  false
monitor_health  healthy (Pi3 state=observing, 정상 주행 중 오탐 없음)
exposure        laptop->Pi1:7400 reachable, laptop->Pi2:7312 NOT reachable (Pi2 listens on 10.77.0.2:7312)
v3_in_trial     seen
attack          response=OK
map_opened      /run/capstone-ivi/map_active (RT 부하 시작)
evidence        Pi3 freeze-frame: suspect(…7426) -> confirmed(…7436)  [CAN 0x200/0x300 원본]
incident        INC-0001 reported (recovery_slot_tamper, expected 5f54018b… != observed be9430b5…) -> approved -> closed
reattack        ERROR gateway closed
final           V2 stable slot A, rolled_back
```

한 번의 실행 결과이며 반복 측정이 아니다. 복원 뒤 Pi3 감시기는 reset nonce를 받아 `observing`/정상으로 돌아갔다(재검증이 실제 Pi3 probation을 거쳤다는 뜻).
오케스트레이터의 결과 파일(`result.json`)에는 판정 시각(`confirm_t`)을 단계로 기록하지 않았다. 다만 이 문장은 처음에 "어디에도 기록되지 않았다"는 뜻으로 썼는데 틀렸다. Pi3의 판정 데이터는 Pi2 디바이스의 `/opt/capstone-ivi/device/progress.json`의 `mismatch` 항목에 `confirm_t`와 함께 저장된다(아래 절 참고).

## 아직 남은 것

- (아래 절에서 갱신함. 이 목록의 사람 승인 실행은 완료)
- Pi1 키의 고정 명령 게이트는 Pi2 `otauser` 계정 기준. root 래퍼(`sudo -n`)를 거치는 점은 이전 시험 구조와 같다.

## 화면·사람 승인 실행 (이어서)

같은 날 이어서 (1) 사람이 관제 화면에서 승인을 누르는 실행과 (2) Pi2 HDMI 모니터에 콕핏 화면을 띄우는 작업을 했다.
노트북은 iPhone 핫스팟에 연결된 상태였고, 실보드 3대에서 실행했다.

콕핏 화면 구성: `bench.incident_field run ... --screen`이 두 가지를 띄운다.
(a) root 권한 feeder: 비공개 Xvfb :93에서 클러스터 화면을 캡처하고, SSH로 Pi3 판정을 읽고, 업데이트 진행 상태를 모은다.
(b) 모니터 자체 데스크톱 세션(Wayland labwc)에 `qmlscene Cockpit.qml` 전체 화면.
둘 다 코어 1-3에 고정했다(클러스터가 코어 0 사용). 임시 프로세스로 띄웠고 재부팅 뒤 자동 시작되지는 않는다.

| 관찰한 문제 | 확인한 원인 | 조치 및 확인 | 상태 |
|---|---|---|---|
| 클러스터 앱을 데스크톱 화면(:0, `xhost`)에 직접 띄우는 방식으로 먼저 시도 | 시작은 됐지만 접근이 틀렸다. 콕핏은 Xvfb의 외부 캡처로 클러스터 화면을 합성하는 구조 | 되돌림(drop-in과 `xhost` 항목 제거) | 철회 |
| 콕핏 포트 이벤트 시각이 노트북 시각(15:21)과 Pi2 시각(06:21)으로 섞여 표시됨 | 이벤트 시각을 노트북에서 찍었음 | feeder가 자기 시계로 찍도록 변경 | 수정 |
| 실제 모니터에서 레이아웃 결함: 지연 막대가 상태 행을 가림, CAN/클러스터 숫자가 아래에서 잘림, 마지막 로그 줄이 잘림 | 실제 모니터에서만 확인됨 | 레이아웃 수정 후 모니터에서 다시 확인 | 수정 |
| 복구 거부 순간 게이지에 "오류"만 표시됨 | 디바이스가 `service_stopped_after_recovery_failure` 다음 `error`를 기록하는데, 게이지가 `error`를 일반 '오류' 문구로만 표시했음 | 명확한 문구로 대응시킴. 내부 단계 이름은 화면에 그대로 보이지 않게 하는 것을 원칙으로 함 | 수정 |
| 오케스트레이터의 백그라운드 스레드가 Pi3 ssh 한 번이 느려서 죽음 | `pi3_verdict`의 `TimeoutExpired`를 처리하지 않았음 | 해당 샘플만 빠진 것으로 처리(예외로 죽지 않음). 재현 시험을 먼저 작성 | 수정 |
| **내 버그**: 승인 후 `result.json`과 Pi3 증적이 저장되지 않고 서비스도 멈추지 않음 | 같은 함수에 이미 `watching`이라는 dict가 있는데 `threading.Event`를 같은 이름으로 추가했고, `finally`의 `watching.set()`이 `AttributeError`를 일으킴 | `gateway_stop`으로 이름 변경, 시험 추가. 다음에 Ctrl-C로 중단한 실행은 `result.json`과 Pi3 증적을 저장함 | 수정 |
| 승인 대기 중인 실행을 내가 중단하자 다음 실행이 `not at a stable V2 baseline`으로 시작을 거부 | 중단으로 Pi2가 `recovery_failed`로 남았고, 기준선 검사가 실행을 막음 | 노트북에서 V2 릴리스를 서빙해 `ivi-repair` 실행. 두 번째에는 Pi2가 이미 stable이었음 | 복구 |
| 서브에이전트 설계 검토 결과: 경보 띠가 게이지 버전 표시와 아래쪽 로그를 약 2.3초 가림, 복구 거부 문구가 불명확, 포트 줄이 5줄 로그에서 밀려남, 영어 로그와 한국어가 섞임, 28 px 미만 글자, 100 ms 한계선 표식에 설명 없음 | 검토에서 확인된 항목 | 수정은 별도로 진행 중이며 이 문서 작성 시점에는 완료로 적지 않음 | 진행 중 |
| 사용자가 둥근 링 게이지 제거를 요청 | 디자인 판단 | 링 제거. 버전 글자의 pop 애니메이션과 경보 띠는 그대로 | 수정 |
| "왜 클러스터를 실시간 창으로 띄우지 않았나"에 대한 설명 문서가 없음. 내가 앞서 "큰 구조 변경"이라고 답했으나 이는 문서 근거 없는 내 추측이었음 | 문서화된 사실은 두 가지: 클러스터는 Xvfb :93에서 숨겨져 돌고, 과거 Qt `grabWindow` 캡처가 CAN 처리를 방해했다(FAILURE_LOG_2026-11-12). 그래서 캡처를 외부에서 2 Hz로 한다 | 직접 표시가 Pi3 측정을 방해하는지는 **아직 측정하지 않았다**. 추측으로 답했던 점을 정정 | 정정 |

### 결과 (사람 승인, 1회)

실행: `--verdict real --decision human --screen`.

```
report          Pi1이 INC-0001 보고
control centre  http://172.20.10.14:8080/ 에서 waiting_for_human
decision        사람이 승인 클릭 (보고 약 9초 뒤, .demo/field-real-screen)
incident        INC-0001 closed (outcome rolled_back)
reattack        Pi1:7400 -> ERROR gateway closed
final           Pi2 V2 / slot A / stable
```

증적 디렉터리(Git 제외): `.demo/field-real-screen`, `.demo/field-real-ui` (두 실행 모두 사람이 승인 클릭; `.demo/field-real-human`은 승인 전에 내가 중단한 실행이라 증적에 넣지 않는다).

### 결과 (Pi3 판정 데이터의 저장 위치)

Pi3의 실제 감지 데이터는 Pi2 `/opt/capstone-ivi/device/progress.json`의 `mismatch` 항목에 디바이스가 저장한다.

```
state           confirmed
cls             freeze
fired rules     latency_limit, latency_p99, cusum, freeze_gap, skip_ratio
monitored_s     35.4
confirm_t       기록됨 (progress.json mismatch 항목)
```

따라서 `confirm_t`는 디바이스에는 기록된다. 기록되지 않는 곳은 오케스트레이터의 `result.json`이다. 위 "결과 (실제 감지...)" 절 끝의 서술을 이에 맞게 고쳤다.

### 측정값

| 항목 | 값 |
|---|---|
| 모니터 | 60 Hz, 2160x1440 |
| 콕핏 `data.json` 쓰기 | 0.5초마다 (2 Hz) |
| QML 읽기 | 400 ms마다 |
| CAN | 20 프레임/초 (0x200 10 Hz, 0x300 에코 10 Hz) |

콕핏 안의 클러스터 그림은 캡처 주기 때문에 초당 2프레임으로 갱신된다.
캡처 간섭 측정(없음 / 0.5초 / 0.1초 / 없음 다시)은 측정 진행 중, 결과는 별도 기록.

### 한계

- 사람 승인·실제 감지 모두 각 1회 실행이며 반복 측정이 아니다.
- 승인을 누른 사람은 프로젝트 작성자다.
- 콕핏 프로세스(feeder, `qmlscene`)는 자동 시작이 아니다. 재부팅하면 다시 띄워야 한다.
- IVI UI는 일부러 바꾸지 않았다. 서명된 애플리케이션이라 바꾸려면 새 V2/V3 릴리스와 새 기준선 해시가 필요하다.
- IVI 홈 카드(음악, 공조, 전화)는 정적 문구이며 실제 데이터가 아니다. 지도 위치와 속도만 기록된 경로 위에서 실제 CAN 0x200 속도로 움직인다.
- 테스트 전체: 마지막 실행은 381 passed, 45 skipped(약 2분 21초). 그 뒤 작은 시험이 몇 개 추가되었고 그 이후 전체 실행은 하지 않았다.

### 아직 남은 것

- 관제 서버가 꺼진 경우(`maintenance_required`)의 실보드 시험
- 반복 측정(사람 승인, 실제 감지 모두 1회)
- 캡처 간섭 측정 결과(진행 중)
- IVI 화면 재설계(시작하지 않음)
- Pi1 키의 고정 명령 게이트는 Pi2 `otauser` 계정 기준. root 래퍼(`sudo -n`)를 거치는 점은 이전 시험 구조와 같다.
