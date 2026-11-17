# Progress Log — 게이트웨이 경유 공격 + 서버 승인 복원

## Session 2026-11-17

설계: docs/superpowers/specs/2026-11-17-gateway-incident-response-design.md
계획: docs/superpowers/plans/2026-11-17-gateway-incident-response.md

### 한 일
1. `Device.repair()` — recovery_failed의 유일한 출구. 커밋 기록과 동일 릴리스·tree hash만 허용.
2. CLI `ivi-repair` / `cluster-repair`.
3. `gateway.py` — Pi1 진단 게이트웨이(중계·기록·닫기). Pi2 정비 서비스 기본 바인드를 127.0.0.1로.
4. `order.py` + `incident_server.py` — 서명된 복원 지시서, 승인 페이지.
5. `incident_responder.py` — 게이트 닫기, 보고, 지시서 검증(1회용), V2 재설치 지시.
6. `demo/incident_drill.py` — 공격 → 거부 → 보고 → 사람 클릭 → 복원 시연.
7. README, docs/GATEWAY_INCIDENT_RESPONSE.md(실보드 체크리스트는 미실행).

### 시행착오 / 결정
- 새 clone에 git 사용자 정보가 없어 첫 커밋이 실패 → 기존 clone과 같은 `jsy202` noreply 신원을 이 저장소에만 설정(전역 설정 불변).
- 기준선: 새 clone에서 `test_local_cockpit_replay` 1건이 PIL 미설치로 실패. 이번 변경과 무관(사전 존재).
- "같은 버전·다른 내용" 테스트의 기대 메시지를 "tree hash"에서 "committed release"로 정정: 압축 sha256이 달라 릴리스 비교에서 먼저 막힌다. tree hash 검사는 이중 방어로 유지.
- `update()`는 서명 검증 뒤 압축을 받기 전에 버전·svn을 검사하므로 `_download_release`에 `precheck` 콜백을 추가해 순서를 보존.
- 시연 첫 수동 실행은 `.demo/` 디렉터리가 없어 셸 리다이렉트가 실패 → 디렉터리 생성 후 재실행.
- 수동 확인: 브라우저 대신 curl로 승인 POST. 접수 화면 해시 표시, 303, rolled_back, V1 미실행, 재공격 거부 확인.

### 아직 안 한 것
- 실보드 실행 전부. 다운그레이드 방어 강화(부팅 전 검사, 기준값 외부 보관). 네임스페이스 기반 Pi1 경유 강제.
