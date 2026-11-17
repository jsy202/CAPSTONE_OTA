# Pi1 게이트웨이 경유 정비 포트 공격과 서버 승인 복원

[보안 시나리오 2번](../README.md#보안-시나리오-세-가지)의 확장이다. 설계는
[설계 문서](superpowers/specs/2026-11-17-gateway-incident-response-design.md),
구현 순서는 [구현 계획](superpowers/plans/2026-11-17-gateway-incident-response.md)에 있다.

## 무엇이 달라졌나

| | 이전 | 지금 |
|---|---|---|
| 공격 경로 | 공격자 → Pi2 정비 포트(7312) 직접 | 공격자 → **Pi1 진단 게이트웨이(7400)** → Pi2 (Pi2 포트는 `127.0.0.1`만 받음) |
| 차단 이후 | `recovery_failed`에서 멈춤, IVI는 꺼진 채 | Pi1이 게이트를 닫고 서버에 보고 → **사람이 승인** → 커밋된 V2만 재설치 → 롤백 완료 |

## 흐름

```text
공격자 ──▶ Pi1 게이트웨이 ──▶ Pi2 정비 서비스   (슬롯 A를 V1로 덮어씀. 요청은 Pi1에 기록됨)
V3 시험 실패(강제) → Pi2: 슬롯 A 해시 ≠ 커밋 기록 → V1 실행 거부 → recovery_failed
Pi1: 게이트 닫기 → 서버에 사고 보고(해시, 출발지, 게이트 기록)
서버(브라우저): [V2로 복원 승인] 클릭 → 서명된 복원 지시서 발급(복원 버전은 서버가 고정)
Pi1: 지시서 검증(서명·차량·nonce·만료·1회용) → 서버에서 V2 원본 수신 → Pi2 `ivi-repair`
Pi2: 서명·커밋 기록과 동일 릴리스·tree hash 확인 → V2 설치 → 외부 재확인 → rolled_back
```

거부하거나 응답이 없으면 IVI는 정지 상태로 남고 결과는 `maintenance_required`다. 클러스터는 별도 앱이라 영향이 없다.

## 실행

```bash
.venv/bin/python -m demo.incident_drill run --output .demo/incident-drill   # 출력된 주소를 브라우저로 열어 클릭
.venv/bin/python -m demo.incident_drill run --output .demo/incident-auto --auto-approve   # 또는 --auto-reject
.venv/bin/python -m pytest -q tests/test_device_repair.py tests/test_cli_repair.py tests/test_gateway.py \
  tests/test_order.py tests/test_incident_server.py tests/test_incident_responder.py tests/test_incident_drill.py
```

증적은 출력 폴더의 `result.json`에 단계별로 남는다(`v1_executed`, `reattack_response`, `gateway_log` 포함).

## 방어가 지키는 것과 지키지 않는 것

- `Device.repair()`는 **커밋 기록과 정확히 같은 릴리스**(버전·svn·sha256)와 tree hash만 받는다. 서버가 실수로 다른
  버전을 승인해도, 서명이 유효한 다른 릴리스를 넣어도 설치 전에 거부된다. `update()`의 버전·svn 규칙은 건드리지 않았다.
- 지시서는 릴리스 서명 키와 **다른** 관제 키로 서명하고, 차량·보고 nonce·만료 시각에 묶인다. 한 번 쓰면 재사용되지 않는다.
- 복원 중 중단되어도 상태는 `recovery_failed`로 남아 다시 `repair`할 수 있다.
- 이 시나리오는 **다운그레이드 방어를 강화한 것이 아니다.** 실행 중인 슬롯을 덮어쓴 뒤 재부팅하면 V1이 실행되는 경로는
  그대로 남아 있다(부팅 전 검사와 기준값의 외부 보관은 이번 범위 밖).

## 한계 (정직하게)

- **V3 실패 판정은 강제**다. Pi3의 실제 CPU 경합 감지가 아니다. 결과 파일에 `forced_verdict: true`로 표시한다.
- 모든 구성요소가 한 대의 노트북에서 도는 프로세스다. **Pi2가 Pi1을 거쳐서만 닿는다는 점은 설정으로 지켜질 뿐
  네트워크로 강제되지 않는다.** 네임스페이스 분리는 후속 작업이다.
- 서버는 HTTP이며 **운영자 로그인이 없다.** 승인 페이지에 접근할 수 있으면 누구나 클릭할 수 있다(실험망 전제).
  다만 다음은 막는다: 다른 웹사이트가 브라우저로 승인 폼을 대신 제출하는 것(`Origin`/`Sec-Fetch-Site` 확인),
  `--api-token-file`을 주면 차량 보고와 결과 전송에 베어러 토큰을 요구한다(토큰 없는 `/outcome`으로 사고를 닫는 것 차단).
  이 토큰은 차량 인증이 아니라 공유 비밀이며, DNS 리바인딩 방어(Host 검사)와 연결 수 제한은 서버 쪽에 아직 없다.
- 게이트웨이는 동시 연결 수(`max_connections`, 기본 16)와 요청 전체 제한 시간(`request_deadline`, 기본 60초)을 둔다.
  정상 속도 이하로 천천히 보내는 연결은 끊긴다. 분산 공격이나 대역폭 소진은 막지 못한다.
- 복원 후 재확인용 Pi3 검증도 시연에서는 항상 통과하는 명령이다.
- Pi1이 사고 보고에 싣는 해시는 Pi2 파일을 같은 기계에서 읽을 수 있을 때만 채워진다. 실보드에서는 `null`일 수 있다.
- 게이트는 사고 후 자동으로 다시 열리지 않는다(정비소에서 수동으로 연다는 가정).

## 실보드 검증 체크리스트 (모두 **미실행**)

보드를 받으면 다음을 순서대로 확인하고, 결과를 이 문서에 날짜와 함께 기록한다.

- [ ] Pi2 정비 서비스를 `--bind 127.0.0.1`로 띄우고, 노트북에서 `Pi2:7312` 직접 접속이 **실패**하는지 확인
- [ ] Pi1에서 `python -m capstone_ota.gateway --upstream <Pi2 접근 경로>`를 띄우고, 노트북 → `Pi1:7400` 공격이 Pi2 슬롯에 도달하는지 확인
  (Pi2가 loopback만 받으므로 Pi1→Pi2 연결 방식을 정해야 한다. SSH 터널 등. 이 부분은 설계에서 열어 둔 항목이다)
- [ ] 실제 Pi3 감시기가 V3 지연을 판정할 때 같은 흐름이 이어지는지(강제 판정 제거)
- [ ] 노트북에서 서버를 띄우고 Pi1→서버 HTTP 보고, 브라우저 승인, 지시서 검증이 실제 LAN에서 되는지
- [ ] 승인 후 `ivi-repair`가 SSH 경유로 Pi2에서 성공하고 `rolled_back`으로 끝나는지
- [ ] 게이트가 닫힌 뒤 재공격이 `ERROR gateway closed`로 거부되는지
- [ ] 서버를 끈 채로 사고를 내서 `maintenance_required`와 콕핏 표시가 의도대로인지
