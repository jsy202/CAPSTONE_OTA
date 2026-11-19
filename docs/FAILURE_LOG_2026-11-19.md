# 시행착오와 검증 기록 — 2026-11-19 (게이트웨이 사고 대응의 실보드 실행)

범위: 11-17에 로컬 프로세스로만 시험한 사고 대응(2-1)을 실제 보드 3대에서 실행하고, 로컬에서 "개발 편의로 가짜였던" 부분을 줄였다.
처음 두 실행은 V3 실패 판정이 강제였고, 같은 날 뒤에 **실제 Pi3 감지 모드(`--verdict real`)**를 추가해 다시 실행했다(아래 "실제 감지" 절). 사람이 브라우저에서 누르는 실행은 하지 않았고 자동 승인으로 시험했다.

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
판정 시각(`confirm_t`)을 결과 파일에 단계로 기록하지는 않았다. 증적은 freeze-frame 파일 이름과 CAN 원본으로만 남아 있다.

## 아직 남은 것

- 사람이 브라우저에서 승인 버튼을 누르는 실보드 실행, 서버가 꺼진 상태의 `maintenance_required` 시험
- 실제 감지 실행의 반복 측정(1회만 실행함)과 `confirm_t` 등 판정 세부의 결과 파일 기록
- Pi1 키의 고정 명령 게이트는 Pi2 `otauser` 계정 기준. root 래퍼(`sudo -n`)를 거치는 점은 이전 시험 구조와 같다.
