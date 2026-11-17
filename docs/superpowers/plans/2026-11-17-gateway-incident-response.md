# 게이트웨이 경유 공격 + 서버 승인 복원 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 정비 포트 공격이 Pi1 진단 게이트웨이를 거쳐 Pi2에 도달하고, 차단 이후 Pi1이 서버에 보고하고, 사람이 브라우저에서 승인하면 커밋된 V2를 해시 검증 후 재설치해 롤백을 완료하는 흐름을 로컬 프로세스로 구현한다.

**Architecture:** Pi2에 `Device.repair()`를 추가해 `recovery_failed`의 유일한 출구로 삼는다(커밋 기록과 tree hash가 정확히 같은 릴리스만 받는다). Pi1 쪽은 `gateway.py`(진단 중계·기록·잠금)와 `incident_responder.py`(보고·지시서 검증·복원 호출)로 나눈다. 서버는 `incident_server.py` 하나가 릴리스 제공, 사고 API, 승인 페이지를 맡는다. 복원 지시서는 `order.py`가 관제 전용 Ed25519 키로 서명하고 검증한다.

**Tech Stack:** Python 3.10+ 표준 라이브러리(`http.server`, `socketserver`, `socket`), 기존 `cryptography`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-gateway-incident-response-design.md`

## Global Constraints

- 새 의존성 금지. 표준 라이브러리 + 기존 `cryptography`만 쓴다.
- `state.json` 키 집합(`phase, committed, pending, min_svn, systemd, result`)은 바꾸지 않는다.
- `Device.update()`의 "더 높은 버전만" 규칙과 svn 하한을 완화하지 않는다.
- 복원은 `state["committed"]`의 `release`와 완전히 같고 tree hash가 `tree_sha256`과 같은 이미지만 실행한다.
- 관제 키는 릴리스 서명 키와 별도다. 개인키는 Git에 넣지 않고, 테스트와 시연은 실행마다 임시 키를 만든다.
- Pi2 정비 서비스는 `127.0.0.1`에만 바인드한다. 공격 스크립트는 게이트웨이 포트만 사용한다.
- 강제한 V3 실패 판정은 결과 JSON과 문서에 `forced` 또는 "강제"로 표시한다.
- 실행: `.venv/bin/python -m pytest -q`. 기존 274개 테스트가 계속 통과해야 한다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다.

## Review Focus

1. **복원 중 중단(전원 차단 흉내):** `repair()`가 슬롯을 교체하는 도중에 예외가 나면 `state.json`은 그대로 `recovery_failed`로 남아야 하고, 다시 `repair()`를 실행할 수 있어야 한다. → Task 1 `test_repair_is_retryable_after_interrupted_install`
2. **지시서 재사용:** 이미 사용한 지시서(같은 incident, 같은 nonce)를 다시 보내면 무시해야 한다. → Task 5 `test_order_is_single_use`
3. **같은 사고 중복 보고:** Pi1이 같은 `recovery_failed`를 두 번 처리해도 서버에 사고가 하나만 생겨야 한다(nonce로 식별). → Task 4 `test_duplicate_report_returns_same_incident`
4. **닫힌 게이트로 긴 업로드:** 닫힌 게이트에 큰 바이트를 보내도 Pi2로 한 바이트도 전달되지 않아야 한다. → Task 3 `test_closed_gateway_forwards_nothing`
5. **서버가 거부한 뒤 승인 시도:** `rejected` 사고에 승인 POST가 오면 409로 거부하고 지시서는 계속 reject여야 한다. → Task 4 `test_cannot_approve_after_reject`

---

### Task 1: Pi2 `Device.repair()`

**Files:**
- Modify: `capstone_ota/device.py` (`update()`의 다운로드·검증 부분을 헬퍼로 분리, `repair()` 추가, `_verify_record`가 관측 해시를 progress에 남김)
- Test: `tests/test_device_repair.py`

**Interfaces:**
- Produces:
  - `Device._download_release(self, url: str, public_key: Path, staging: Path) -> dict` — `release.json`/`release.sig`/`app.tar.gz`를 `staging`에 받고 서명과 압축 해시를 검증한 뒤 release dict를 돌려준다. `update()`가 이걸 쓰도록 바꾸고, 기존 progress 단계 이름(`metadata`, `signature`, `signature_verified`, `download`, `archive_verify`)은 그대로 둔다. 버전·svn 규칙은 `update()`에 남긴다.
  - `Device.repair(self, url: str) -> dict` — 반환은 `self.status()`. 고정된 `trusted.pub`만 쓴다.
  - progress의 `recovery_failed` 단계에 `observed_tree_sha256: str | None`, `expected_tree_sha256: str` 필드를 남긴다.

- [ ] **Step 1: 실패하는 테스트 작성** — 픽스처는 `test_bundle`의 `keys`, `payload`와 `test_device`의 `serving`을 재사용한다. 기존 `tests/test_debug_port_rollback_scenario.py`의 흐름(V2 커밋 → V3 시험 중 슬롯 A를 다른 서명 이미지로 교체 → 실패 판정 → `recovery_failed`)을 헬퍼 `_tampered_device(tmp_path)`로 만든다. 이 헬퍼는 `(device, url)`을 돌려주고, `ClusterDevice`의 verifier는 호출 0 통과, 1 실패, 2 이후 통과로 둔다.

```python
def test_repair_restores_committed_release(...):
    device, url = _tampered_device(tmp_path)
    assert device.status()["result"] == "recovery_failed"
    result = device.repair(url + "/v2")
    assert result["result"] == "rolled_back" and result["version"] == "2.0.0"
    assert result["phase"] == "stable" and result["active_slot"] == "A"

def test_repair_rejects_other_version(...):        # url + "/v3" → ValueError, state 바이트 불변, slots/A 해시 불변
def test_repair_rejects_same_version_other_content(...):  # 같은 "2.0.0"/svn 2, 다른 payload로 서명 → ValueError("... tree hash ...")
def test_repair_rejects_bad_signature(...):        # 다른 키로 서명한 v2 → ValueError, 불변
def test_repair_refuses_when_not_recovery_failed(...):  # 정상 커밋 상태에서 repair → ValueError
def test_repair_is_retryable_after_interrupted_install(...):
    # monkeypatch os.replace를 슬롯 교체 시 한 번만 OSError로 → repair 실패
    # state.json["result"] == "recovery_failed" 유지, 두 번째 repair는 rolled_back
def test_recovery_failed_progress_records_observed_hash(...):
    # progress.json의 expected_tree_sha256 == committed tree hash, observed != expected
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest -q tests/test_device_repair.py` → `AttributeError: ... 'repair'`
- [ ] **Step 3: `_download_release` 분리** — `update()`가 이 헬퍼를 쓰게 바꾼다. 동작은 바꾸지 않는다. `.venv/bin/python -m pytest -q tests/test_device.py tests/test_debug_port_rollback_scenario.py` → 전부 PASS
- [ ] **Step 4: `repair()` 구현** — 순서: `_locked()` → `_load()` → `phase=="trial" and result=="recovery_failed"`가 아니면 `ValueError("repair is only for a recovery_failed device")` → 임시 폴더에서 `_download_release(url, trusted.pub, staging)` → `release != committed["release"]`면 거부 → `staging/payload`에 extract → `tree_hash != committed["tree_sha256"]`면 `ValueError("repair image tree hash mismatch")` → 커밋 슬롯을 교체한다(`rmtree` 후 `os.replace`, update와 같은 fsync) → `_event("repair_installed")` → `return self._recover(state)` 뒤 `status()`. 거부할 때는 `_event("repair_rejected")`만 남기고 `state.json`은 쓰지 않는다.
- [ ] **Step 5: `_verify_record` 관측 해시** — 불일치를 판정하기 직전에 관측값을 계산해 `self._progress_data`에 넣는다. `_recover`의 `recovery_failed` progress 호출에 그대로 실린다.
- [ ] **Step 6: 통과 확인** — `.venv/bin/python -m pytest -q tests/test_device_repair.py tests/test_device.py tests/test_debug_port*.py tests/test_ivi*.py` → PASS
- [ ] **Step 7: 커밋** — `git commit -m "feat(device): add repair() as the only exit from recovery_failed"`

### Task 2: CLI `ivi-repair` / `cluster-repair`

**Files:**
- Modify: `capstone_ota/cli.py`
- Test: `tests/test_cli_repair.py`

**Interfaces:**
- Consumes: `Device.repair(url) -> dict` (Task 1)
- Produces: `python -m capstone_ota --root <dir> ivi-repair <url> (--check-command JSON | --initialization-only)` — `ivi-update`와 같은 검증 옵션을 받는다(복원 후 Pi3 재확인에 필요). stdout에는 `status()` JSON 한 줄과 `validation` 필드를 낸다. 종료 코드: `rolled_back`이면 2(기존 규칙), 거부되면 1.

- [ ] **Step 1: 테스트** — Task 1의 `_tampered_device`로 만든 root에 subprocess로 CLI를 실행한다.

```python
def test_cli_repair_rolls_back(...):
    proc = run_cli(root, "ivi-repair", url + "/v2", "--check-command", json.dumps(PASS_CMD))
    out = json.loads(proc.stdout); assert proc.returncode == 2 and out["result"] == "rolled_back"
def test_cli_repair_rejects_wrong_release(...):  # /v3 → returncode 1, stderr "OTA error:", state 불변
```
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_cli_repair.py` → argparse `invalid choice`
- [ ] **Step 3: 구현** — `ivi-update` 파서 정의를 `cluster-repair`/`ivi-repair`에 재사용한다(URL 필수, `--public-key`와 `--systemd` 없음). 디스패치는 `device.repair(args.url)`이다.
- [ ] **Step 4: 통과 확인** — `pytest -q tests/test_cli_repair.py` → PASS
- [ ] **Step 5: 커밋** — `git commit -m "feat(cli): expose ivi-repair/cluster-repair"`

### Task 3: Pi1 진단 게이트웨이

**Files:**
- Create: `capstone_ota/gateway.py`
- Modify: `capstone_ota/debug_port.py` (`main()`의 `--bind` 기본값을 `127.0.0.1`로)
- Test: `tests/test_gateway.py`

**Interfaces:**
- Consumes: `debug_port.DebugPortServer`, `debug_port.push` (기존 프로토콜: `<slot>\n<size>\n<bytes>`, 응답 한 줄)
- Produces:
  - `class DiagnosticGateway(socketserver.ThreadingTCPServer)`: `__init__(self, address, upstream: tuple[str, int], log_path: Path, connection_timeout: float = 10.0)`
  - `gateway.is_open -> bool`, `gateway.close(reason: str) -> None` (스레드 안전, `threading.Event`)
  - `gateway.log_tail(n: int = 20) -> list[dict]` — 각 줄: `{"time", "source": "ip:port", "slot", "size", "forwarded": bool, "response"}`
  - `main()`: `python -m capstone_ota.gateway --listen 0.0.0.0:7400 --upstream 127.0.0.1:7312 --log <path>`

- [ ] **Step 1: 테스트**

```python
def test_gateway_relays_push_and_logs(tmp_path):
    # DebugPortServer(127.0.0.1:0, root) + DiagnosticGateway(127.0.0.1:0, upstream=그 주소)
    assert push("127.0.0.1", gw_port, "A", archive) == "OK"
    assert (root / "slots/A/bin/app").exists()
    entry = gw.log_tail()[-1]; assert entry["slot"] == "A" and entry["forwarded"] and entry["response"] == "OK"
def test_closed_gateway_forwards_nothing(tmp_path):
    gw.close("incident"); 
    assert push(... 1MB archive ...).startswith("ERROR gateway closed")
    assert not (root / "slots/A").exists(); assert gw.log_tail()[-1]["forwarded"] is False
def test_gateway_relays_upstream_error(tmp_path):  # slot "C" → 응답이 "ERROR slot must be A or B"로 그대로 전달
def test_debug_port_default_bind_is_loopback():    # main의 argparse 기본값 확인
```
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_gateway.py` → `ModuleNotFoundError`
- [ ] **Step 3: 구현** — 핸들러는 두 줄 헤더를 `readline` 상한(8, 16바이트)으로 읽는다. 닫혀 있으면 기록하고 거부한다. 열려 있으면 upstream에 연결해 헤더를 그대로 보내고, `size` 바이트를 스트리밍한 뒤 응답 한 줄을 되돌려준다. 기록은 `O_APPEND` JSON 줄로 하고 `log_tail`은 파일 끝에서 읽는다.
- [ ] **Step 4: 통과 확인** — `pytest -q tests/test_gateway.py tests/test_debug_port.py` → PASS
- [ ] **Step 5: 커밋** — `git commit -m "feat(gateway): Pi1 diagnostic gateway relays and logs maintenance-port traffic"`

### Task 4: 복원 지시서 서명 + 사고 서버

**Files:**
- Create: `capstone_ota/order.py`, `capstone_ota/incident_server.py`
- Test: `tests/test_order.py`, `tests/test_incident_server.py`

**Interfaces:**
- Produces (`order.py`):
  - `sign_order(order: dict, private_key: Path) -> tuple[bytes, bytes]` — `(json bytes(sort_keys), signature)`
  - `verify_order(raw: bytes, signature: bytes, public_key: Path, *, vehicle: str, nonce: str, now: float) -> dict` — 서명을 먼저 검증한 뒤 `unique_json`으로 파싱하고 키 집합, `vehicle`, `nonce`, `expires > now`, `action in {"restore","reject"}`을 확인한다. 실패하면 `ValueError`.
  - 지시서 키 집합: `{"incident", "vehicle", "nonce", "action", "release", "version", "expires"}`. reject면 `release`/`version`은 `null`. 만료 기본값은 발급 후 300초.
- Produces (`incident_server.py`):
  - `class IncidentServer(ThreadingHTTPServer)`: `__init__(self, address, releases_dir: Path, store: Path, order_key: Path)`
  - `GET /releases/<label>/<file>` → `releases_dir` 정적 제공(경로 이탈 금지)
  - `POST /incidents` (JSON 보고서) → `201 {"id": str}`. 같은 `nonce`가 이미 있으면 `200` + 기존 id
  - `GET /` → HTML 목록(2초 자동 새로고침, 사고마다 승인/거부 form)
  - `POST /incidents/<id>/approve|reject` → 상태 전이, `303` 리다이렉트. 승인할 때 `release` 라벨은 **서버 설정 `release_labels: dict[version, label]`**에서 보고서의 `committed_version`으로 찾는다. 요청 본문의 값은 무시한다. 상태가 이미 결정됐으면 `409`.
  - `GET /incidents/<id>/order` → 미결정이면 `204`, 결정됐으면 `200 {"order": b64(raw), "signature": b64(sig)}`
  - `POST /incidents/<id>/outcome` (`{"result": ...}`) → 기록하고 상태를 `closed`로
  - `main()`: `python -m capstone_ota.incident_server --bind 127.0.0.1:8080 --releases <dir> --store <json> --order-key <pem> --release-label 2.0.0=v2 ...`

- [ ] **Step 1: 테스트**

```python
# test_order.py
def test_round_trip(...):            # sign → verify OK
def test_rejects_wrong_key / wrong_vehicle / wrong_nonce / expired / tampered_bytes / unknown_action(...)

# test_incident_server.py
def test_report_approve_then_signed_restore_order(...):
    id = post("/incidents", REPORT)["id"]; assert get(f"/incidents/{id}/order").status == 204
    post_form(f"/incidents/{id}/approve", {"version": "1.0.0"})   # 본문 버전은 무시되어야 함
    order = verify_order(...); assert order["action"] == "restore" and order["version"] == "2.0.0" and order["release"] == "v2"
def test_reject_produces_reject_order(...)
def test_cannot_approve_after_reject(...):          # 409, 지시서는 reject 유지
def test_duplicate_report_returns_same_incident(...)
def test_index_lists_incident_with_hashes(...):     # GET / 에 expected/observed 해시 문자열 포함
def test_releases_path_traversal_blocked(...):      # /releases/../store.json → 404
def test_unknown_committed_version_cannot_be_approved(...):  # 라벨 매핑 없음 → 409
```
- [ ] **Step 2: 실패 확인** — `pytest -q tests/test_order.py tests/test_incident_server.py` → `ModuleNotFoundError`
- [ ] **Step 3: `order.py` 구현** — Ed25519 로딩은 `bundle.verify`와 같은 방식이고, `unique_json`을 재사용한다.
- [ ] **Step 4: `incident_server.py` 구현** — 저장은 `device.atomic_write`로 JSON 한 파일에 한다. HTML은 `html.escape`로 모든 보고서 값을 이스케이프한다. 페이지 상단에 "V3 실패 판정은 시험용 강제" 문구를 고정한다.
- [ ] **Step 5: 통과 확인** — 위 두 파일 PASS
- [ ] **Step 6: 커밋** — `git commit -m "feat(server): incident server with human approval and signed recovery orders"`

### Task 5: Pi1 사고 대응기

**Files:**
- Create: `capstone_ota/incident_responder.py`
- Test: `tests/test_incident_responder.py`

**Interfaces:**
- Consumes: `DiagnosticGateway.close/log_tail` (Task 3), `verify_order` (Task 4), 서버 API (Task 4), `relay.cache_release`, `relay.serving_release`, `relay.target_command`의 SSH 실행 방식
- Produces:
  - `class IncidentResponder`: `__init__(self, server_url: str, vehicle: str, order_public_key: Path, release_public_key: Path, gateway, target: dict, decision_timeout: float = 300.0, poll_interval: float = 1.0)`
  - `handle(self, result: dict) -> dict` — `result`는 `relay.forward()`나 CLI가 낸 Pi2 결과다. `result["result"] != "recovery_failed"`면 `{"action": "none"}`. 그 외에는 `{"action": "restored"|"maintenance_required", "incident": id, "repair": <Pi2 결과 또는 None>, "reason": str}`
  - `target` dict는 기존 `forward`의 target JSON(`host`, `checkout`, `python`, `root`, `check_command`, `application`)과 같다. 복원 명령은 `<python> -m capstone_ota --root <root> ivi-repair <relay_url> --check-command <json>`이다.
  - 사용한 `(incident, nonce)`는 인스턴스 집합에 기록해 재사용을 거부한다.

- [ ] **Step 1: 테스트** — 진짜 `IncidentServer`, `DiagnosticGateway`, Task 1의 `_tampered_device` root를 쓴다. SSH는 `demo/ivi_attack.py`의 PATH ssh 대체 방식을 픽스처로 쓴다.

```python
def test_recovery_failed_closes_gate_reports_and_restores_on_approval(...):
    # 별도 스레드에서 서버에 사고가 생기면 approve POST
    out = responder.handle(tampered_result)
    assert not gateway.is_open and out["action"] == "restored" and out["repair"]["result"] == "rolled_back"
    assert server_store["incidents"][out["incident"]]["status"] == "closed"
def test_rejection_leaves_device_and_reports_maintenance(...):   # Pi2 state 불변, action maintenance_required
def test_timeout_is_maintenance_required(...):                    # decision_timeout=0.5
def test_forged_order_is_ignored(...):        # 다른 키로 서명한 지시서를 응답하는 가짜 서버 → maintenance_required, repair 미호출
def test_order_is_single_use(...):            # 같은 지시서를 두 번 → 두 번째는 ValueError/무시
def test_non_failure_result_does_nothing(...):  # {"result": "rolled_back"} → action none, gate open
```
- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError`
- [ ] **Step 3: 구현** — 보고서는 스펙의 "사고 보고 내용" 키를 따른다. `observed`/`expected` 해시는 `result`에 없으면 Pi2 `progress.json`에서 읽고(로컬 경로일 때만), 없으면 `null`로 둔다. 보고 nonce는 `secrets.token_hex(16)`. 폴링은 `poll_interval` 간격으로 `decision_timeout`까지 한다. 복원과 거부 모두 결과를 `/outcome`으로 보고한다.
- [ ] **Step 4: 통과 확인** — PASS
- [ ] **Step 5: 커밋** — `git commit -m "feat(responder): Pi1 isolates, reports and restores only on a signed approval"`

### Task 6: 시연 `demo/incident_drill.py` + 종단 테스트

**Files:**
- Create: `demo/incident_drill.py`
- Test: `tests/test_incident_drill.py`

**Interfaces:**
- Consumes: Task 1~5 전부, `demo/ivi_attack.py`의 V1 마커 패턴(`old-app-executed`), `dashboard.ivi.build.payload`
- Produces: `run(output: Path, decision: str = "human") -> dict` (`decision in {"human","approve","reject"}`), CLI `python -m demo.incident_drill run --output <dir> [--auto-approve|--auto-reject]`. `result.json` 키: `steps`(단계별 시각과 이벤트), `v1_executed: bool`, `reattack_response: str`, `final: dict`, `forced_verdict: true`, `scope: "local processes; Pi1 routing is configuration, not network isolation"`.

- [ ] **Step 1: 테스트**

```python
def test_drill_auto_approve_restores_v2(tmp_path):
    r = run(tmp_path / "out", decision="approve")
    assert r["final"]["result"] == "rolled_back" and r["final"]["version"] == "2.0.0"
    assert r["v1_executed"] is False and r["reattack_response"].startswith("ERROR gateway closed")
    assert any(e["slot"] == "A" and e["forwarded"] for e in r["gateway_log"])  # 공격이 실제로 게이트웨이를 거침
def test_drill_auto_reject_requires_maintenance(tmp_path):
    r = run(tmp_path / "out", decision="reject")
    assert r["final"]["result"] == "recovery_failed" and r["response"]["action"] == "maintenance_required" and r["v1_executed"] is False
```
- [ ] **Step 2: 실패 확인** — `ModuleNotFoundError`
- [ ] **Step 3: 구현** — 스펙의 "시연 명령" 1~9단계를 그대로 따른다. 가짜 Pi3는 `test_debug_port_rollback_scenario.py`의 `VERIFIER_SCRIPT` 패턴을 쓰되, 공격은 **게이트웨이 포트**로 V1 `app.tar.gz`를 푸시한다. `human` 모드면 `http://127.0.0.1:<port>/`를 출력하고 결정을 기다린다. 끝나면 모든 서버를 종료한다.
- [ ] **Step 4: 통과 확인** — `pytest -q tests/test_incident_drill.py` → PASS
- [ ] **Step 5: 사람 클릭 수동 확인** — `.venv/bin/python -m demo.incident_drill run --output .demo/incident-$(date +%Y%m%d-%H%M)`을 실행하고, 브라우저에서 승인을 누른 뒤 `result.json`의 `final.result == "rolled_back"`을 확인한다.
- [ ] **Step 6: 커밋** — `git commit -m "feat(demo): gateway incident drill with browser approval"`

### Task 7: 문서 + 전체 검증

**Files:**
- Modify: `README.md` ("보안 시나리오" 2번, "현재 한계와 남은 일", 코드 읽는 순서 표에 `gateway.py`/`incident_responder.py`/`incident_server.py`/`order.py` 추가), `docs/VALIDATION.md` (실보드 단계 추가, "미실행" 표시)
- Create: `.planning/2026-11-17-gateway-incident-response/progress.md` (기존 관례, 시행착오 기록)

- [ ] **Step 1: README 갱신** — 흐름도(공격자 → Pi1 게이트웨이 → Pi2, 사고 보고 → 승인 → 재설치), 실행 명령, 그리고 한계 세 가지를 적는다: 강제 판정, localhost라서 Pi1 경유는 네트워크로 강제되지 않음, 실보드 미검증.
- [ ] **Step 2: VALIDATION.md** — 실보드 체크리스트: Pi2 정비 서비스 loopback 바인드 확인, 노트북에서 Pi2:7312 직접 접속 실패 확인, Pi1:7400 경유 공격, 승인 후 V2 복원, 재공격 거부.
- [ ] **Step 3: 전체 검증** — `.venv/bin/python -m pytest -q` → 실패 0(기존 274 + 신규), `git diff --check` 출력 없음, 변경 파일 개인키 스캔 `grep -rl "BEGIN PRIVATE KEY" $(git diff --name-only main)` 출력 없음
- [ ] **Step 4: 커밋** — `git commit -m "docs: gateway incident response scenario and hardware checklist"`
