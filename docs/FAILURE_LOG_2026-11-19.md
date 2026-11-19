# 시행착오와 검증 기록 — 2026-11-19 (게이트웨이 사고 대응의 실보드 실행)

범위: 11-17에 로컬 프로세스로만 시험한 사고 대응(2-1)을 실제 보드 3대에서 실행하고, 로컬에서 "개발 편의로 가짜였던" 부분을 줄였다.
**V3 실패 판정은 아직 강제**다(Pi3 실제 감지 연결은 별도 작업). 사람이 브라우저에서 누르는 실행은 이번에 하지 않았고 자동 승인으로 시험했다.

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

## 아직 남은 것

- V3 실패 판정을 실제 Pi3 감시기 + probation 판정으로 교체 (이 실행은 `forced_verdict: true`)
- 사람이 브라우저에서 승인하는 실보드 실행, 서버가 꺼진 상태의 `maintenance_required` 시험
- 복원 후 재검증이 실보드에서는 아직 "항상 통과" 명령 (Pi3 probation 재검증으로 대체 필요)
- Pi1 키의 고정 명령 게이트는 Pi2 `otauser` 계정 기준. root 래퍼(`sudo -n`)를 거치는 점은 이전 시험 구조와 같다.
