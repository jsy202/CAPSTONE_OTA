# 게이트웨이 경유 정비 포트 공격과 서버 승인 복원 설계

작성일: 2026-11-17 · 상태: 사용자 승인 대기

## 의도와 이유

보안 시나리오 2번(정비 포트를 이용한 복구 슬롯 바꿔치기)은 지금 두 가지가 비현실적이거나 미완성이다.

1. **공격 경로:** 공격자가 Pi2의 정비 포트(7312)에 LAN으로 직접 붙는다. 실차에서 진단 요청은
   OBD 포트에서 중앙 게이트웨이를 거쳐 도메인 ECU로 전달된다. 그래서 Pi1(Central)을 게이트웨이로
   두고, 진단 요청이 Pi1을 거쳐 Pi2로 가도록 바꾼다.
2. **방어 후 조치:** V1 실행을 막은 뒤 `recovery_failed`에서 멈춘다. 그 뒤로는 업데이트를 시도할
   때마다 실패한 복구를 반복하고, IVI는 꺼진 채로 남는다. 사고 보고, 관제 요원의 승인, 원본 재설치를
   거쳐 IVI를 정상 버전으로 되돌리는 흐름을 추가한다. UN R155의 보안 모니터링·대응 개념과 연결된다.

실보드가 없으므로 **노트북 한 대에서 프로세스로 보드를 흉내 내어 최대한 구현**하고, 실보드 검증은
나중에 보드가 생기면 별도로 한다.

## 목표

- 공격 스크립트 → Pi1 진단 게이트웨이 → Pi2 정비 서비스 경로로 바꿔치기 공격이 실제로 일어난다.
- Pi2는 기존 tree hash 재검증으로 바꿔친 슬롯을 거부한다. 기존 동작을 그대로 유지한다.
- Pi1은 Pi2의 `recovery_failed`를 감지하면 진단 게이트를 닫고 서버에 사고를 보고한다.
- 서버는 브라우저 페이지에 사고를 보여 주고, 사람이 **[V2로 복원 승인]** 또는 **[거부·입고 지시]**를 클릭한다.
- 승인하면 서버가 Ed25519로 서명한 복원 지시서를 발급한다. Pi1은 서명을 검증하고 서버에서 원본
  릴리스를 받아 Pi2로 전달한다. Pi2는 `repair()`로 원본을 설치하고 기록된 tree hash와 정확히
  같을 때만 실행한다.
- 거부하거나 서버가 응답하지 않으면 IVI는 정지 상태로 두고 "정비 필요"로 표시한다.
- 전 과정을 pytest로 자동 검증한다(승인 클릭은 HTTP POST로 대체). CI에서도 돈다.
- 시연용 명령 하나로 브라우저에서 클릭하며 따라갈 수 있다.

## 비목표

- 다운그레이드 방어 강화(부팅 전 검사, 기준값의 외부 보관): 사용자가 이번 범위에서 제외했다.
- 네트워크 네임스페이스 등으로 Pi1 경유를 네트워크 수준에서 강제하는 일. 후속 작업으로 둔다.
- 실제 UDS/DoIP 프로토콜 구현. 진단 게이트웨이는 기존 정비 포트 프로토콜을 그대로 중계한다.
- 실제 CPU 경합 판정. V3 실패는 기존 공격 시험처럼 강제한다.
- 실보드 배포와 실측.
- 서버 인증(로그인), HTTPS. 기존 README가 밝힌 대로 실험망 HTTP이다.

## 반목표 (기술적으로 동작해도 실패인 경우)

- 복원 경로가 V2 외의 이미지(V1, 다른 정식 서명 릴리스)를 실행할 수 있게 되는 것.
  `repair()`는 서버 승인 여부와 무관하게 커밋 기록과 정확히 같은 tree hash만 받아야 한다.
- 서명 없는 지시서나 다른 차량·다른 nonce의 지시서로 복원이 시작되는 것.
- 기존 `update()`의 "더 높은 버전만" 규칙이나 svn 하한을 완화하는 것.
- 시연이 실제 측정을 한 것처럼 보이는 것. 강제한 판정은 화면과 문서에 강제라고 표시한다.

## 제약

- 기존 `Device`, `ClusterDevice`/`IviDevice`, `relay`, `bundle`, `debug_port`, `can_verdict`를 재사용한다.
- 의존성 추가 없이 Python 표준 라이브러리 HTTP 서버와 기존 `cryptography`만 쓴다.
- 기존 274개 테스트를 깨지 않는다. `state.json` 스키마는 기존 기기와 호환되어야 한다
  (`_load`가 키 집합을 엄격히 검사하므로 새 키를 추가하지 않는다).
- 개인키는 Git에 넣지 않는다. 시연과 테스트는 실행마다 임시 키를 만든다.

## 검토한 방법

| 방법 | 내용 | 장단점 | 결정 |
|---|---|---|---|
| A. 보드별 프로세스 | 서버·Pi1·Pi2·Pi3 역할을 로컬 프로세스/스레드로 실행, localhost 포트로 연결 | 빠름, 기존 코드 재사용, CI 가능 / Pi1 경유가 설정상 약속일 뿐 | **채택** |
| B. 네트워크 네임스페이스 | `ip netns`로 보드별 네트워크 분리 | Pi2가 실제로 Pi1 뒤에 숨음 / sudo 필요, CI 번거로움 | A 이후 후속 |
| C. 화면 재생 확장 | `local_cockpit_replay`에 합성 이벤트 추가 | 로직이 돌지 않음 | 제외 |

복원 방식도 두 가지를 비교했다. 자동 복원 + 서버 보고만 하는 방식은 시연이 끊기지 않지만 서버가
판단하는 장면이 없다. **서버 승인 후 복원**은 실제 관제(VSOC) 흐름에 가깝고, 원본을 서버에서 받으므로
Pi1 보관 기능도 필요 없다. 사용자가 **서버 승인, 사람이 직접 클릭**을 선택했다.

## 사용자가 정한 것

- 공격 경로를 Pi1 경유로 바꾼다.
- 방어 후 조치는 서버 승인 후 복원이고, 승인은 사람이 브라우저에서 클릭한다.
- 실보드 없이 방법 A로 최대한 구현하고, 실보드 시험은 나중에 한다.

## 설계

### 구성

```
[브라우저] ── http://127.0.0.1:<서버포트>/ ──┐
                                             ▼
[서버 = 노트북]  capstone_ota/incident_server.py
   · /releases/<label>/  기존 서명 릴리스 제공
   · POST /incidents      사고 보고 수신
   · GET  /               사고 목록 + 승인/거부 버튼
   · POST /incidents/<id>/approve | reject
   · GET  /incidents/<id>/order   승인 시 서명된 복원 지시서
        ▲ HTTP
[Pi1 = 게이트웨이]  capstone_ota/gateway.py
   · 진단 게이트(OBD 역할 TCP 포트): 정비 포트 요청을 Pi2로 중계, 요청마다 출발지·슬롯·크기 기록
   · 게이트 상태: open(리콜 후 열린 채로) / closed(사고 후 격리)
   · 사고 대응: Pi2 결과가 recovery_failed → 게이트 닫기 → 사고 보고 → 지시서 대기
                → 지시서 검증 → 서버에서 원본 받기 → Pi2 repair 호출
        ▲ 공격           │ 내부 연결(localhost, Pi2 정비 서비스는 127.0.0.1에만 바인드)
[공격 스크립트]           ▼
                  [Pi2]  기존 IviDevice + debug_port + 새 repair()
                          ▲ 판정(기존 방식: verifier_command, 선택적으로 vcan0 HMAC)
                  [Pi3 역할]  V2는 통과, V3 시험 중에 공격을 일으키고 실패 보고(강제)
```

### Pi2: `Device.repair(url, order)` (device.py)

- 잠금을 잡고 `state.json`을 읽는다. `phase == "trial"`이고 `result == "recovery_failed"`일 때만 동작한다.
  그 외 상태면 거부한다.
- 받은 릴리스를 기존 방식대로 검증한다. 고정된 `trusted.pub`로 서명을 확인하고, 압축 해시가
  `release.json`과 같은지 본다.
- `release`가 `state["committed"]["release"]`와 **완전히 같아야** 한다(버전, svn, sha256).
- 압축을 임시 폴더에 풀고 tree hash가 `state["committed"]["tree_sha256"]`과 같을 때만 커밋 슬롯을
  교체한다. 다르면 아무것도 바꾸지 않고 거부한다.
- 교체 후에는 기존 `_recover(state)`를 그대로 호출한다. 재검증, 활성화, Pi3 재확인, `rolled_back`이
  모두 기존 경로로 진행된다.
- 이벤트 `repair_started`, `repair_installed`, `repair_rejected`를 `events.jsonl`과 progress에 남긴다.
- **지시서 서명은 Pi1이 검증한다.** Pi2의 최종 안전장치는 tree hash 일치이고, 지시서를 위조하더라도
  커밋된 V2 외에는 실행할 수 없다. (빌더 판단 여지: Pi2도 지시서를 받아 이중 검증할 수 있으나 필수는 아님.)
- CLI에 `capstone-ota ivi-repair <릴리스URL>` 하위 명령을 추가해 Pi1이 SSH로 호출할 수 있게 한다.
  출력은 기존 `status()` JSON 한 줄이다.

### Pi1: `capstone_ota/gateway.py`

- `DiagnosticGateway(listen, pi2_debug_addr, log_path)`: TCP를 받아 Pi2 정비 서비스로 바이트를 중계한다.
  첫 줄(슬롯 이름)과 둘째 줄(크기)을 읽어 출발지 IP·포트와 함께 JSON 줄로 기록한다. 게이트가 닫혀 있으면
  `ERROR gateway closed`로 즉시 거부하고 그 시도도 기록한다.
- `IncidentResponder(server_url, vehicle_id, server_public_key, gateway, pi2)`
  - `handle(result)`: 기존 `forward()`가 돌려준 Pi2 결과를 받는다. `recovery_failed`이면
    1) 게이트를 닫고, 2) 진단 기록 중 최근 항목과 Pi2 상태로 사고 보고를 만들어 POST하고,
    3) 지시서를 기다린다(폴링, 제한 시간 기본 300초, 시연 시 조절 가능).
  - 지시서는 `{"incident": id, "vehicle": vehicle_id, "nonce": 보고 시 Pi1이 만든 값,
    "action": "restore", "release": "<label>", "version": "2.0.0", "expires": epoch}`이고
    서버 키로 서명되어 있다. 검증 항목은 서명, 차량, nonce, 만료, action이다. 하나라도 틀리면 무시한다.
  - 검증을 통과하면 기존 `cache_release`로 서버에서 원본을 받아 임시로 제공하고, Pi2에 `ivi-repair`를 실행한다
    (기존 `forward`와 같은 SSH 경로, 로컬에서는 기존 ssh 대체 방식).
  - 거부(`action: "reject"`)나 시간 초과면 Pi2를 그대로 둔다. 서버에 상태를 보고하고 결과를
    `maintenance_required`로 남긴다.
  - 복원 결과(`rolled_back` 또는 실패)를 서버에 다시 보고한다. **게이트는 다시 열지 않는다.**
    정비소에서 수동으로 여는 것으로 본다.

### 서버: `capstone_ota/incident_server.py`

- `ThreadingHTTPServer` 하나로 릴리스 정적 제공과 사고 API를 함께 처리한다.
- 사고는 메모리와 JSON 파일(`incidents.json`)에 저장한다. 상태는 `reported → approved | rejected → closed`이다.
- 승인 페이지는 의존성 없는 HTML 한 장이다. 사고마다 시각, 차량, 유형, 대상 슬롯, 기대 해시와 실제 해시,
  출발지, 게이트 상태, 현재 상태를 보여 준다. 승인/거부는 form POST로 처리하고, 페이지는 2초마다 새로고침한다.
- 승인하면 서버 개인키(릴리스 서명 키와 **별도인** 관제 키)로 지시서에 서명한다. 복원 버전은 사고 보고에
  들어 있는 커밋 버전으로 고정하고, 사람이 버전을 고를 수 없게 한다.

### 사고 보고 내용

`{"vehicle", "time", "type": "recovery_slot_tamper", "slot", "committed_version", "committed_svn",
"expected_tree_sha256", "observed_tree_sha256", "detail", "gateway_log_tail": [...], "gateway_state",
"cluster": "unaffected", "nonce"}`. 실제 해시는 Pi2의 `recovery_failed` 원인 문자열이나 progress에서 얻는다.
얻을 수 없으면 `null`로 둔다. 빌더는 `_verify_record`가 관측값을 progress에 남기도록 최소한으로 바꿀 수 있다.

### 시연 명령: `demo/incident_drill.py`

`python -m demo.incident_drill run --output .demo/incident-<날짜> [--auto-approve|--auto-reject]`

1. 임시 릴리스 키와 관제 키를 만들고, V1/V2/V3를 서명해 둔다.
2. 서버, Pi2 정비 서비스(127.0.0.1), Pi1 게이트웨이를 띄우고 브라우저 주소를 출력한다.
3. V2를 설치·커밋한다.
4. V3 시험 중에 가짜 Pi3가 **공격 스크립트를 실행**한다. 공격 스크립트는 게이트웨이 포트로 V1 압축을
   커밋 슬롯에 밀어 넣는다. 그 뒤 가짜 Pi3가 실패를 보고한다.
5. Pi2가 복구 직전 해시 불일치로 거부한다(`recovery_failed`).
6. Pi1이 게이트를 닫고 서버에 보고한다. 공격 스크립트가 다시 시도하면 `gateway closed`로 거부된다.
7. 사람이 브라우저에서 승인한다(또는 옵션으로 자동 처리).
8. Pi1이 지시서를 검증하고 V2를 재설치하며, Pi2가 해시 일치를 확인해 V2로 롤백을 완료한다.
9. `result.json`에 단계별 증적을 남긴다. V1 실행 여부 마커(`old-app-executed`)는 끝까지 없어야 한다.

### 테스트

- `tests/test_device_repair.py`: 정상 복원, 다른 버전 거부, 같은 버전·다른 내용 거부, 서명 오류 거부,
  `recovery_failed`가 아닌 상태에서 거부, 거부 시 슬롯과 `state.json` 불변.
- `tests/test_gateway.py`: 중계와 기록, 닫힌 게이트 거부, 응답 중계.
- `tests/test_incident_server.py`: 보고 → 승인 → 서명된 지시서, 거부, 사람이 버전을 바꿀 수 없음.
- `tests/test_incident_responder.py`: 위조 서명·다른 nonce·만료·다른 차량 지시서 무시, 시간 초과 시
  `maintenance_required`.
- `tests/test_incident_drill.py`: 전체 흐름 자동 승인 → `rolled_back`, V1 미실행, 재공격 차단.
  자동 거부 → `maintenance_required`.
- 선택: `vcan0`가 있으면 Pi3 판정을 `can_verdict`로 전달하는 변형(없으면 skip, 기존 관례).

### 문서

- README "보안 시나리오" 2번 갱신: Pi1 경유, 사후 조치 흐름, 강제 판정과 localhost 한계 명시.
- 실보드 검증 체크리스트(`docs/`)에 이 시나리오의 실보드 단계를 추가하되 "미실행"으로 표시한다.

## 빌더에게 맡기는 것

- 모듈 내부 구조, 폴링 간격, 로그 형식의 세부 사항.
- 실제 해시를 사고 보고에 싣는 방법(progress 필드 추가 등 최소 변경).
- 승인 페이지 모양(의존성 없는 단순 HTML이면 충분).
- 지시서 직렬화 형식(기존 `release.json`/`release.sig`처럼 JSON + 분리 서명을 권장).
