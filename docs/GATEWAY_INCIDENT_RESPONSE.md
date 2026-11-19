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
- 로컬 시험(`demo.incident_drill`)은 모든 구성요소가 한 대의 노트북에서 도는 프로세스다. 실보드 실행에서는 Pi2의 정비
  포트가 이더넷 주소에만 열려 노트북에서 직접 닿지 않는 것을 확인했다.
- 서버는 HTTP이며 **운영자 로그인이 없다.** 승인 페이지에 접근할 수 있으면 누구나 클릭할 수 있다(실험망 전제).
  다만 다음은 막는다: 다른 웹사이트가 브라우저로 승인 폼을 대신 제출하는 것(`Origin`/`Sec-Fetch-Site` 확인),
  `--api-token-file`을 주면 차량 보고와 결과 전송에 베어러 토큰을 요구한다(토큰 없는 `/outcome`으로 사고를 닫는 것 차단).
  이 토큰은 차량 인증이 아니라 공유 비밀이며, DNS 리바인딩 방어(Host 검사)와 연결 수 제한은 서버 쪽에 아직 없다.
- 게이트웨이는 동시 연결 수(`max_connections`, 기본 16)와 요청 전체 제한 시간(`request_deadline`, 기본 60초)을 둔다.
  정상 속도 이하로 천천히 보내는 연결은 끊긴다. 분산 공격이나 대역폭 소진은 막지 못한다.
- 로컬 시연의 복원 후 재검증은 슬롯 트리 해시를 확인하는 `demo.restore_check`다. 실보드 시험은 아직 "항상 통과" 명령을
  쓴다(Pi3 probation 재검증은 미연결).
- Pi1이 사고 보고에 싣는 해시는 Pi2의 `ivi-update` 응답에서 온다(파일을 읽지 않는다).
- 게이트는 사고 후 자동으로 다시 열리지 않는다(정비소에서 수동으로 연다는 가정).

## 실보드 검증 (2026-11-19 일부 실행)

도구: `python -m bench.incident_field setup|run|teardown CONFIG` (설정 파일은 Git 제외). `setup`은 보드마다 별도 venv에
현재 코드를 설치하고, Pi1→Pi2 전용 SSH 키를 **고정 명령 게이트**(`capstone_ota/ota_gate.py`)와 함께 등록한다.
이 키는 `from=`으로 Pi1 주소에 묶이고, 고정된 `ivi-update`/`ivi-repair` 한 가지 외에는 `refused`(126)로 거부된다.
`run --verdict forced`는 V3의 시험 결과를 스크립트로 실패시킨다(**실제 CPU 경합 감지가 아님**, 결과의 `forced_verdict: true`).

실행 환경: 노트북 = 공격자 + 관제 서버, Pi1 `wlan0`/`eth0`, Pi2는 이더넷(10.77.0.2)에서만 정비 포트를 연다.

- [x] Pi2 정비 서비스를 이더넷 주소에만 바인딩하고, 노트북(Wi-Fi)에서 `Pi2:7312` 직접 접속이 **실패**하는지 확인 (`ss`로 `10.77.0.2:7312`만 listen)
- [x] 노트북 → `Pi1:7400` 공격이 Pi1→이더넷→Pi2 슬롯 A에 도달 (응답 `OK`, 출발지 `172.20.10.14`가 게이트웨이 로그에 기록됨)
- [x] Pi2가 변조를 거부하고 **자기 응답에** 기대/관측 해시를 실어 보냄 (`5f54018b…` ≠ `be9430b5…`), Pi1은 Pi2 파일을 읽지 않음
- [x] 노트북 서버에 Pi1→HTTP 보고, 지시서 검증이 실제 LAN에서 동작 (**승인은 자동 승인(`--decision approve`)으로 시험**, 사람이 브라우저에서 누르는 실행은 아직 안 함)
- [x] 승인 후 `ivi-repair`가 진짜 SSH(제한된 키)로 Pi2에서 성공, `rolled_back` / V2 / 슬롯 A로 종료
- [x] 게이트가 닫힌 뒤 재공격이 `ERROR gateway closed`로 거부
- [ ] 실제 Pi3 감시기가 V3 지연을 판정할 때 같은 흐름이 이어지는지(강제 판정 제거)
- [ ] 사람이 브라우저에서 승인 버튼을 누르는 실보드 실행
- [ ] 서버를 끈 채로 사고를 내서 `maintenance_required`와 콕핏 표시가 의도대로인지

실보드에서 처음 드러난 결함(시험이 아니라 보드에서만 보였다): 복원 명령이 **갱신의 검증 명령**(V3 시험, 25초 강제 실패)을
그대로 재사용해 게이트가 거부했다. 갱신의 검증과 복원 후 재검증은 다른 명령이어서 대상 설정에 `repair_check_command`를 분리했다.
자세한 내용은 [FAILURE_LOG_2026-11-19.md](FAILURE_LOG_2026-11-19.md).
