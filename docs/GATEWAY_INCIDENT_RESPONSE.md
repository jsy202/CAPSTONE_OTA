# Pi1 게이트웨이 경유 정비 포트 공격과 서버 승인 복원

[보안 시나리오 2번](../README.md#보안-시나리오-세-가지)의 확장이다. 설계는
[설계 문서](superpowers/specs/2026-11-17-gateway-incident-response-design.md),
구현 순서는 [구현 계획](superpowers/plans/2026-11-17-gateway-incident-response.md)에 있다.

## 무엇이 달라졌나

| | 이전 | 지금 |
|---|---|---|
| 공격 경로 | 공격자 → Pi2 정비 포트(7312) 직접 | 공격자 → **Pi1 진단 게이트웨이(7400)** → Pi2 (Pi2 포트는 이더넷 주소 `10.77.0.2`만 받음, `127.0.0.1`이 아님) |
| 차단 이후 | `recovery_failed`에서 멈춤, IVI는 꺼진 채 | Pi1이 서버에 보고 → **사람이 승인** → 커밋된 V2만 재설치 → 롤백 완료 → **그 뒤 게이트를 닫음** |

## 흐름

```text
공격자 ──▶ Pi1 게이트웨이 ──▶ Pi2 정비 서비스   (커밋된 릴리스가 든 복구 슬롯을 V1로 덮어씀. 요청은 Pi1에 기록됨)
V3 시험 실패(강제 또는 실제 판정) → Pi2: 복구 슬롯 해시 ≠ 커밋 기록 → V1 실행 거부 → recovery_failed
Pi1: 서버에 사고 보고(해시, 출발지, 게이트 기록) — 이때 게이트는 아직 열려 있다
서버(브라우저): [V2로 복원 승인] 클릭 → 서명된 복원 지시서 발급(복원 버전은 서버가 고정)
Pi1: 지시서 검증(서명·차량·nonce·만료·1회용) → 서버에서 V2 원본 수신 → Pi2 `ivi-repair`
Pi2: 서명·커밋 기록과 동일 릴리스·tree hash 확인 → V2 설치 → 외부 재확인 → rolled_back
Pi1: 복원이 끝난 뒤(또는 거부·시간 초과·실패로 끝난 뒤) 게이트를 닫는다. 복원은 이 포트가 아니라 Pi1의 제한된 SSH 키로 가므로 포트가 열려 있어도 복원에는 영향이 없다(2026-11-20 변경: 이전에는 차단 직후에 닫았다).
```

거부하거나 응답이 없으면 IVI는 정지 상태로 남고 결과는 `maintenance_required`다. 클러스터가 영향을 받지 않는다고 단정하지 않는다. 2026-11-19~20 실보드 실행에서 Pi3 감시기는 지도 작업 프로세스가 도는 동안 클러스터의 에코 지연이 약 1~3 ms에서 약 900~1800 ms로 올라가는 것을 측정했다(이것이 V3 실패 판정의 근거다). 사고 보고의 `cluster: "unaffected"` 필드는 코드에 박힌 상수이며 측정값이 아니다. 같은 이유로 보고의 `type: recovery_slot_tamper`도 코드의 상수다.

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

- `--verdict forced`의 V3 실패 판정은 강제다(결과 `forced_verdict: true`). `--verdict real`은 Pi3 감시기가 실제로 판정하며 `forced_verdict: false`다. 정차 구간의 판정만 롤백/복구 거부로 이어진다.
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
- 게이트는 사고가 끝난 뒤 닫히고 자동으로 다시 열리지 않는다(정비소에서 수동으로 연다는 가정). 복원이 끝나기 전까지 열려 있는 구간이 있으며, 그동안의 요청은 게이트 기록에 남는다.

## 실보드 검증 (2026-11-19 일부 실행)

도구: `python -m bench.incident_field setup|run|teardown CONFIG` (설정 파일은 Git 제외). `setup`은 보드마다 별도 venv에
현재 코드를 설치하고, Pi1→Pi2 전용 SSH 키를 **고정 명령 게이트**(`capstone_ota/ota_gate.py`)와 함께 등록한다.
이 키는 `from=`으로 Pi1 주소에 묶이고, 고정된 `ivi-update`/`ivi-repair` 한 가지 외에는 `refused`(126)로 거부된다.
`run --verdict forced`는 V3의 시험 결과를 스크립트로 실패시킨다(**실제 CPU 경합 감지가 아님**, 결과의 `forced_verdict: true`).

실행 환경: 노트북 = 공격자 + 관제 서버, Pi1 `wlan0`/`eth0`, Pi2는 이더넷(10.77.0.2)에서만 정비 포트를 연다.

- [x] Pi2 정비 서비스를 이더넷 주소에만 바인딩하고, 노트북(Wi-Fi)에서 `Pi2:7312` 직접 접속이 **실패**하는지 확인 (`ss`로 `10.77.0.2:7312`만 listen)
- [x] 노트북 → `Pi1:7400` 공격이 Pi1→이더넷→Pi2 복구 슬롯에 도달(당시 기준선에서는 슬롯 A) (응답 `OK`, 출발지 `172.20.10.14`가 게이트웨이 로그에 기록됨)
- [x] Pi2가 변조를 거부하고 **자기 응답에** 기대/관측 해시를 실어 보냄 (`5f54018b…` ≠ `be9430b5…`), Pi1은 Pi2 파일을 읽지 않음
- [x] 노트북 서버에 Pi1→HTTP 보고, 지시서 검증이 실제 LAN에서 동작 (이 항목의 첫 실행은 자동 승인(`--decision approve`)이었다. 사람이 누른 실행은 아래 별도 항목)
- [x] 승인 후 `ivi-repair`가 진짜 SSH(제한된 키)로 Pi2에서 성공, `rolled_back` / V2 / 슬롯 A로 종료
- [x] 게이트가 닫힌 뒤 재공격이 `ERROR gateway closed`로 거부 (2026-11-20부터 닫는 시점은 '복원 뒤'이고, 이 순서로 실보드 1회 확인: 게이트 기록에 요청들 다음 `gateway_closed`, 이어서 재공격 거부) (2026-11-20에 닫는 시점을 '복원 뒤'로 바꿨고, 이 순서로는 실보드 재확인을 아직 하지 않았다)
- [x] 실제 Pi3 감시기가 V3 지연을 판정할 때 같은 흐름이 이어지는지(`--verdict real`, 2026-11-19 1회, `forced_verdict: false`, Pi3 freeze-frame suspect→confirmed 확인; 반복 측정 아님)
- [x] 사람이 브라우저에서 승인 버튼을 누르는 실보드 실행 (2026-11-19, `--verdict real --decision human --screen`, 1회. Pi1이 INC-0001 보고 → 관제 화면 `waiting_for_human` → 사람이 승인 클릭(보고 약 9초 뒤) → `rolled_back`, 재공격 `ERROR gateway closed`, Pi2 V2/슬롯 A/stable. 증적 디렉터리(Git 제외): `.demo/field-real-screen`, `.demo/field-real-ui` (두 실행 모두 사람이 승인 클릭; `.demo/field-real-human`은 승인 전에 내가 중단한 실행이라 증적에 넣지 않는다). 승인한 사람은 프로젝트 작성자)
- [x] 2026-11-19~20 밤 재검증(IVI 재설계 후, 기준선 V2 2.1.0이 슬롯 B): 클릭·승인은 모두 사람이 아니라 보조 도구(XTEST 클릭, `--decision approve`)가 했다. 결과는 [FAILURE_LOG_2026-11-19.md](FAILURE_LOG_2026-11-19.md)의 "야간 검증" 절
- [ ] 서버를 끈 채로 사고를 내서 `maintenance_required`와 콕핏 표시가 의도대로인지

실보드에서 처음 드러난 결함(시험이 아니라 보드에서만 보였다): 복원 명령이 **갱신의 검증 명령**(V3 시험, 25초 강제 실패)을
그대로 재사용해 게이트가 거부했다. 갱신의 검증과 복원 후 재검증은 다른 명령이어서 대상 설정에 `repair_check_command`를 분리했다.
자세한 내용은 [FAILURE_LOG_2026-11-19.md](FAILURE_LOG_2026-11-19.md).

## 이 절차가 보여주는 것과 보여주지 않는 것 (2026-11-20 정정)

- 롤백 거부는 **복구 슬롯이 변조되어 있기 때문에** 생긴다. CPU 경합 감지는 롤백이 일어나게 만들 뿐이며, 변조된 슬롯이 있으면 어떤 이유의 롤백이든 같은 거부가 나온다.
- 이미지를 쓰는 동작 자체는 막지 않는다. 정비 포트는 이 사고를 재현하려고 일부러 인증을 두지 않았다. 막는 것은 롤백 시점의 tree hash 비교다.
- 사고 보고에 적히는 슬롯은 처음에 실패한 V3 시험 슬롯이었고 변조된 슬롯과 달랐다. 2026-11-20에 `Device.recovery_evidence()`가 `checked_slot`(해시를 비교한 슬롯)을 돌려주도록 고쳤다.
- 공격 대상 슬롯은 더 이상 A로 고정하지 않는다. `bench.incident_field.recovery_slot()`이 커밋된 릴리스가 든 슬롯을 고른다(`result.json`의 `attack_slot`).
- 공격은 하지만 지도를 열지 않는 대조 실행은 하지 않았다(커밋된 V3는 새 릴리스 버전 없이 되돌릴 수 없다).
- 기기의 취약 모드(`recovery_verification=False`, 이벤트 `VULNERABLE_MODE_RECOVERY_UNVERIFIED`)로 V1 라디오 화면이 실제로 실행되는 "방어가 뚫리는" 시연은 아직 하지 않았다.
- 커밋된 슬롯이 변조된 채 phase가 `stable`이면 기기는 업데이트도 `ivi-repair`도 스스로 하지 못한다. 미해결 제품 공백이며 이번에는 커밋된 릴리스를 슬롯에 직접 풀어 수동으로 고쳤다.
- 각 실행은 1회이며 통계가 아니다.
