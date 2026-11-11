# Progress Log

## Session: 2026-11-11

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-11-11

### Actions Taken
-

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|

### Errors
| Error | Resolution |
|-------|------------|

## Session 2026-11-11
- Ran planning-with-files init-session for named plan `2026-11-11-debug-port-rollback-defense`.
- Read capstone_ota/{bundle,device,probation,ivi,cluster,process}.py, bench/{physical,verify_service,load_phases}.py, demo/ivi_attack.py.
- Key discovery: the core defense (pinned tree-hash re-verification before executing the recovery slot, Device._verify_record called from Device._recover before _activate) already exists in device.py. The real gap is the attack delivery vector (always-open debug port) and combining real CPU contention with the slot-swap attack, which the project README itself flags as not yet done.
- Wrote findings.md (full trace of the existing commit->trial->probation->recover flow) and task_plan.md (8 phases, Phase 1 complete, Phase 8 marked optional/stretch).
- No code changed yet. Next: Phase 2 (vulnerable-mode toggle + baseline unit tests).

## Session 2026-11-11 (continued, autonomous implementation)
- Installed the superpowers plugin (user + marketplace) for future sessions; used ponytail and ponytail-review skills live in this session; ran a code-only (no API key) graphify extraction of the repo to cross-check caller lists for Device.__init__/_verify_record against the manual grep audit -- confirmed no missed callers (demo/can_bench.py:RemoteDevice, demo/run.py:VerifiedDevice both call super().__init__(root) with no positional conflict). Removed the generated graphify-out/ afterward (not meant to be committed).
- Implemented Phase 2: Device(root, recovery_verification=True) toggle, ClusterDevice/IviDevice pass it through, _verify_record skips its check and logs VULNERABLE_MODE_RECOVERY_UNVERIFIED when off. Two new tests in tests/test_device.py. Found while testing that _recover()'s stable-phase short-circuit branch re-verifies the committed slot BEFORE any trial even starts (stronger/earlier defense than planned) -- documented in findings.md and task_plan.md.
- Implemented Phase 3: capstone_ota/debug_port.py, a stdlib-only always-open unauthenticated TCP service that can only overwrite one slot directory (slot_path() structurally restricted to root/slots/<A|B>, reuses bundle.extract's existing safe extraction). tests/test_debug_port.py (now 4 tests).
- ponytail-review pass found a real gap: no per-connection timeout, so a stalled client could hold a handler thread forever on an "always open" service. Fixed with a 10s default connection_timeout + regression test. Confirmed via code audit (grepping cli.py/relay.py construction sites and relay.py's strict config key allow-list) that recovery_verification is unreachable from any CLI flag, env var, or the forward command's remote-target JSON schema -- it is Python-construction-only.
- Implemented Phase 4: no new code needed; debug_port.push() doubles as the attack client (ponytail: reuse, don't build a second thing that does the same job).
- Implemented Phase 5: tests/test_debug_port_rollback_scenario.py, combining ClusterDevice + a fake-Pi3 verifier subprocess + the real debug port. First draft tried running the V3 trial in a background Python thread with a sentinel-file wait, and hit `ValueError('signal only works in main thread...')` because device.py's download deadline uses signal.SIGALRM (main-thread only). Fixed by moving the attack trigger into the verifier subprocess itself (it calls debug_port.push() on its own first real invocation, i.e. exactly when "Pi3" is evaluating the V3 trial) -- simpler and more precisely timed than threading ever was. Both defended and vulnerable-mode outcomes proven in one file.
- Implemented Phase 6 (code only): PhysicalBench.debug_port_rollback() in bench/physical.py, following scenario()'s existing idioms exactly (systemd-run units, self.fixture reuse, self.cluster.run). Added test_SEC_005_... to tests/hardware/test_physical_can.py. Verified only that it imports and that `pytest tests/hardware` collects/skips it cleanly (10 skipped, was 9) -- NOT run against real boards this session (not confirmed reachable). Flagged explicitly as unverified/draft in task_plan.md, progress.md, and the README.
- Implemented Phase 7: new README section ("디버그 포트를 이용한 복구 슬롯 바꿔치기") with the professor-facing sentence, the trust-boundary fields, the toggle's unreachability, run commands, and a new bullet in "현재 한계와 남은 일" noting the real-board run is pending.
- Final state: 266/266 non-hardware tests pass (`pytest tests/ --ignore=tests/hardware`); hardware suite collects cleanly (10 skipped, no boards configured). Working tree diff is clean (no stray generated files). Phase 8 (optional old-key/K1 stretch) intentionally not started -- core defense already holds regardless of which key signed the planted image (see findings.md).
- Nothing was committed to git this session; all changes are in the working tree of this worktree (feature/physical-can-ota), ready for the user to review and commit.

## Session 2026-11-11 (real-hardware test run, trial-and-error log)
- Boards reachable on SK Wi-Fi (Pi1 192.168.45.171, Pi2 .250, Pi3 .223). Disabled a
  pre-existing, unrelated crash-looping legacy service on Pi1
  (`capstone-ota-agent.service`, 902 restarts, old MQTT broker no longer exists) --
  `sudo systemctl disable --now`.
- Deployed this session's code to Pi2's bench runtime venv only (Pi1/Pi3 don't need
  it -- they never run debug_port.py or the toggle): built a fresh wheel
  (`python -m build --wheel`), uploaded, `pip install --force-reinstall --no-deps`
  into `/home/otauser/capstone-ota-bench-1793549816/venv`. Also had to push
  `bench/load_phases.py` into that runtime checkout (only the wheel was deployed
  there before; `bench/` didn't exist at all on the board).
  - Error: `pip install .../capstone_ota-0.1.1-dev.whl` -> "is not a valid wheel
    filename" (pip enforces the PEP 427 name pattern). Fix: re-upload keeping the
    original `capstone_ota-0.1.0-py3-none-any.whl` filename instead of renaming.
- Ran `test_SEC_005_...` (my new Phase 6 test) against `.demo/three-board-config.json`
  -> failed at the very first step, the plain V2 baseline install (before any of my
  new code even runs): `status.result == "failed"`, `verification: []`.
- Isolated whether this was caused by my changes: ran the EXISTING, unmodified
  `test_OTA_CAN_002...[compatible]` scenario -> same identical failure. Confirmed:
  this is a pre-existing real-hardware environment problem, not something this
  session's debug-port work caused or touched.
- First hypothesis (wrong, but worth recording): `/dev/dri/card0`/`renderD128` had an
  ACL for `otauser` but not for `capstone-app` (the user the app's `--check` actually
  runs as via `runuser`), so guessed a GPU/EGL permission issue. Added
  `capstone-app` to `video`,`render` groups (`usermod -aG`). Re-ran the same baseline
  scenario -> **same failure**, so this was not the (sole) cause. Reading the app's
  own wrapper script afterward showed `--check` forces `QT_QPA_PLATFORM=offscreen`
  and `QT_QUICK_BACKEND=software` specifically to avoid needing the GPU at all --
  the group fix was a plausible-but-wrong lead. Left in place (harmless, may still
  matter for the display service's own EGL warnings), but it did not fix this bug.
- Reproduced directly, bypassing the whole bench/SSH/relay chain: ran the real ARM
  Qt binary's own `--check` by hand as `capstone-app` on a leftover device slot from
  a failed run (`/opt/capstone-cluster/runs/.../device/slots/A/libexec/dashboard
  --check`) with the exact env vars the wrapper sets
  (`QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`). Result: exit code 2, only
  benign `QStandardPaths: XDG_RUNTIME_DIR not set` printed to stderr, no other
  output -- the binary itself is failing near-silently on `--check` right now.
- Status: root cause not yet found. Continuing to narrow it down (next: check whether
  XDG_RUNTIME_DIR actually needs to exist/be writable for capstone-app, and whether
  CAPSTONE_QT_PREFIX is actually set anywhere the real automated flow would see it
  but our manual SSH probe didn't).

- Root cause found by reading dashboard/qt_cluster/main.cpp: `--check`'s code path
  (lines 137-151) is self-contained and never touches CAN/sockets at all. It polls
  `uiReady()` (QML property `capstoneGaugeReady` + the `capstoneSpeedNeedle` visual
  item existing + incubation finished) every 20ms for 3 consecutive ticks, and calls
  `app.exit(2)` if that doesn't happen within 3000ms. Exit 2 with no printed error
  (confirmed earlier) means: window+panel DID load (no `component.errors()` warning,
  which prints on that failure path) -- the QML UI simply never reports itself ready
  within 3s when run `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software` on this
  board right now.
- This is a pre-existing bug in the real-hardware `--check` path, unrelated to and
  not caused by this session's debug-port work (confirmed by reproducing on the
  unmodified `scenario('compatible')` test too). It blocks ALL real-hardware OTA
  tests right now, not just the new one. Did not chase further into main.qml /
  capstoneGaugeReady (`dashboard/qt_cluster/qml/`) this session -- stopping here to
  report back rather than open-ended QML debugging on live hardware.
- Net result this session: Phase 6 code (debug_port_rollback) is deployed and wired
  correctly (confirmed it never even got exercised -- the bench fails before
  reaching it, at the same point an untouched existing scenario also fails), but
  genuinely UNVALIDATED end-to-end due to this separate, pre-existing blocker.
  `capstone-app` was added to `video`,`render` groups as a (harmless, but ultimately
  not the fix) side effect of the first wrong hypothesis.

- Narrowed further: ruled out (a) a 3s UI-readiness timeout (process actually exits
  in ~70-190ms, nowhere near 3000ms) and (b) offscreen-vs-xcb platform mismatch
  (tried `QT_QPA_PLATFORM=xcb` against the real running Xvfb `:93`/`Xauthority` that
  the live `capstone-cluster.service` unit itself uses -- same instant exit 2).
  Checked `capstone-cluster.service`'s actual unit file: it does NOT set
  CAPSTONE_QT_PREFIX either, so that was a dead end too (system Qt5 is the intended
  runtime, not a custom prefix).
  Given component.errors() (main.cpp:130) would print on a QML *parse* error and
  nothing prints at all, the remaining code path is main.cpp:125
  (`engine.rootObjects().isEmpty()`) -- i.e. `qrc:/main.qml` most likely fails to
  resolve/load at all in this exact binary, not a runtime QML logic error.
- Did not go further (would mean inspecting the compiled binary's Qt resource table
  or rebuilding from dashboard/qt_cluster/build.py, which risks much more live-board
  time without a proper edit/rebuild/redeploy loop). This matches the project's own
  note that the latest single-fixed-cluster cleanup was never deployed/tested on
  real hardware before today -- most likely explanation is a resource-path or build
  regression from that cleanup, not anything from this session's debug-port work.
- Reported back to the user with the precise finding and stopped here rather than
  attempting a blind C++/QML fix on live hardware.

## Session 2026-11-11 (continued: root-caused and fixed the real blocker, reran everything)
User said "너가 알아서 끄내셈" (figure it out yourself) after the earlier stop. Continued:
- Confirmed via `strings`/`gdb` the binary's qrc resources and `qrc:/main.qml` string
  were present (ruled out "missing resource" theory). Wrote a small LD_PRELOAD shim
  to intercept `exit`/`_exit` for a backtrace -- never fired (glibc's own internal
  exit path from `return` in `main()` doesn't go through the public symbols);
  abandoned that approach rather than chase glibc internals further.
- Found `qmake`+`g++`+the pinned Volvo source checkout already present on Pi2
  (`/home/otauser/echo-build`, a prior native ARM build directory) -- no
  cross-compiler needed, rebuilt a `CONFIG+=debug` variant of the CURRENT repo's
  main.cpp directly on Pi2 natively.
- `gdb -batch -ex "break main.cpp:125" ... -ex continue`: hit the breakpoint at
  `if (engine.rootObjects().isEmpty()) return 2;`, then `continue` and it SUCCEEDED
  (`{"initialized":true,"ui":"VolvoQt"}`). The act of pausing under gdb was enough
  to let the race resolve in the app's favor -- confirmed root objects really were
  racing against the synchronous check, not a logic bug.
- Fixed `dashboard/qt_cluster/main.cpp`: added a bounded (`QCoreApplication::
  processEvents()` + `QThread::msleep(5)`, up to 100 tries = 500ms) wait before
  giving up on an empty `rootObjects()`, instead of checking exactly once. Added
  `#include <QThread>`.
- Verified the fix on Pi2 without gdb: rebuilt (debug, then release-mode) and ran
  10/10 clean (both as root and as the real `capstone-app` service user, which
  needed a world-readable copy since the build dir is otauser:700).
- Pulled the fixed release binary to the laptop
  (`.demo/pi-qt-2026-11-11-fixed/capstone-qt-cluster-aarch64`), made
  `.demo/three-board-config-fixed.json` pointing `arm_binary` at it.
- Re-ran the full hardware suite: now got PAST the baseline (previous blocker
  resolved) but hit a SECOND, separate pre-existing issue: `verification: [{"error":
  "actor exited unsuccessfully"}]` on every scenario that uses the 3-board
  `--verifier` path. Traced it to two stale SSH host aliases still carrying
  iPhone-hotspot-era IPs from before this session's Wi-Fi migration:
  `capstone-verifier` (Pi2's root ssh config, pointed at old Pi3 IP
  172.20.10.4) and `capstone-source` (Pi3's otauser ssh config, pointed at old
  Pi1 IP 172.20.10.2). Fixed both (`sed -i` the IPs to the current
  192.168.45.x addresses); verified both aliases connect.
- Re-ran the full hardware suite again: **9/10 passed** -- every pre-existing
  scenario (CAN_001, OTA_CAN_002[compatible], OTA_CAN_002[attack], CAN_003,
  CAN_004, SEC_003, SEC_004, SEC_005-missing-witness, NET_001) now passes for the
  first time with the single-fixed-cluster cleanup actually deployed. Only my
  OWN new `test_SEC_005_debug_port...` test failed, with `status['result'] ==
  'committed'` instead of `'recovery_failed'` -- not a security regression, a
  test-design bug: `debug_port_rollback()`'s background-thread timing (`time.sleep(5)`
  racing bench/load_phases.py's ramp-up) never actually produced a trial failure,
  so nothing ever triggered a rollback for the debug-port swap to be caught by.
- Investigated further: `PhysicalBench` has never had real `Probation`/
  `monitor.detect` p99 CPU-contention wiring at all -- `new_device()` only ever
  builds a functional `verify-service.py`-based checker, which doesn't look at
  latency. `capstone_ota.monitor.service` is not deployed/running on Pi3 (checked
  `systemctl list-units` there, not present). Decided NOT to deploy and wire a
  whole new real-time monitor service this session (too large/open-ended without
  the user present to confirm design choices); instead redesigned
  `debug_port_rollback()` to force the V3-trial failure deterministically via its
  own `check_command` override (same mechanism `scenario('attack')` already uses),
  explicitly documented as NOT yet gated by genuine CPU contention, with wiring
  real `Probation` into `PhysicalBench` logged as a separate, named follow-up.
- First redesign attempt reused a shared verifier-call counter file across the V2
  (default checker) and V3 (custom checker) calls, assuming "call 1" would be V3 --
  wrong, because the default checker never touches that counter, so the custom
  script's first-ever call (for V3) saw count 0, not 1, and never fired the attack.
  Fixed by dropping the counter entirely: the custom check_command is ONLY ever
  used for the V3 call, so it unconditionally pushes the attack and reports failure.
- That attempt still failed because `self.fixture`'s archive is root:600
  (uploaded that way in `prepare()` for the existing in-process attack fixture),
  but my check_command runs as the SSH-login user (otauser), which couldn't read
  it -- `push()`'s `read_bytes()` would raise `PermissionError`. Fixed by staging
  an otauser-readable (`chmod 644`) copy of the fixture archive before referencing
  it from the script.
- **Final result: `pytest tests/hardware/test_physical_can.py --hardware-config
  .demo/three-board-config-fixed.json` -> 10 passed, 0 failed.** Full non-hardware
  suite re-confirmed clean afterward: 266 passed.
- Updated task_plan.md (Phase 6 marked fully complete, with the Probation-wiring
  gap named explicitly as a separate follow-up) and README.md (new accurate
  real-hardware results, removed the stale "단일 클러스터 정리는 보드에 배포하지
  않았다" line, documented the race-condition fix and the stale-SSH-alias fix).
- Nothing committed to git yet; all changes remain in this worktree's working tree
  (`feature/physical-can-ota`), including `dashboard/qt_cluster/main.cpp` (the race
  fix) and `bench/physical.py` (the final `debug_port_rollback()`).

## Session 2026-11-11 (filler task while user away: OS-level A/B attempt)
User said to try an OS-level build if nothing else was left to do. Attempted to
install `rauc` locally (`sudo apt-get install rauc`) to build a real `.raucb` demo
bundle -- blocked: this laptop has no passwordless sudo, and there's no pip/source
fallback without meson+ninja+glib build deps. Did not try to bypass this.
Produced a safe, non-executing design scaffold instead (no board touched):
`docs/OS_LEVEL_AB_DESIGN.md` (why RAUC over Yocto, how the existing
_verify_record pinned-hash defense extends to whole-partition slots, explicit
TODO order) and `deploy/os-ab/{system.conf.template,README.md}` (placeholder RAUC
config, clearly marked unused/untested/placeholder device paths).

## rauc 로컬 포맷 검증 (사용자 복귀 후, sudo 비밀번호 제공받음)

- 사용자가 sudo 비밀번호를 줘서 `apt-get install rauc` 성공 (이전엔 비밀번호 없어 막혔음).
- 보드는 건드리지 않고, 로컬 PC(`/tmp` 스크래치패드)에서만: 자체 서명 테스트 CA/인증서 생성 →
  더미 페이로드로 `.raucb` 번들 생성 → `rauc info`로 서명 검증까지 확인. 포맷/도구체인이
  이 환경에서 동작한다는 것만 확인한 것이고, 실제 배포 아티팩트는 아님.
- 결과물을 `deploy/os-ab/local-format-test.raucb`, `deploy/os-ab/local-test-ca.cert.pem`로
  보존하고, `docs/OS_LEVEL_AB_DESIGN.md`·`deploy/os-ab/README.md`에 "로컬 포맷 검증 완료,
  보드 작업은 여전히 미착수"로 반영.
- 남은 블로커는 더 이상 "rauc 설치"가 아니라 "보드 SD카드 복제 후 파티션 구조 변경"이며,
  이건 사용자 부재 중 시도하지 않음 (실패 시 보드 안 부팅 위험).

## 와이파이 경유 검증 결과 전달 → CAN 버스 인증 전달로 교체 (사용자 요청)

- 사용자가 "Pi2-Pi3 검증 왕복이 와이파이(SSH)를 거치는 게 맞냐"고 질문 → 코드 확인 결과,
  슬롯 활성화 시마다(`ClusterDevice._activate`) SSH로 Pi3한테 물어보고 그 stdout의
  `passed` 값을 그대로 신뢰하는 구조였음. 평상시 부팅엔 안 걸리고 업데이트/복구 시도 중에만
  걸린다는 점은 확인했지만, 그 구간에서는 와이파이 방해로 업데이트를 영구 봉쇄하거나 SSH
  조작으로 가짜 PASS를 만들 수 있는 실제 공격 표면이었음.
- AskUserQuestion으로 세 가지 방향 제시 → 사용자가 "CAN 버스로 검증 결과 전달" 선택.
- `capstone_ota/can_verdict.py` 신설: nonce + HMAC-SHA256 서명을 CAN 프레임(ID 0x7F1)에
  실어 보내는 `send_verdict`/`open_listener`/`await_verdict`/`receive_verdict`.
- **시행착오 1**: 처음엔 `receive_verdict()`를 SSH 트리거 *이후에* 호출했다가
  `test_can_verdict_accepts_a_genuine_authenticated_pass`가 `result=='failed'`로 실패.
  원인: CAN은 방송이라 리스너가 바인딩되기 전에 보낸 프레임은 그냥 사라짐 — Pi3가 검증 끝내고
  바로 신호를 쏘는데 Pi2는 SSH 서브프로세스가 끝난 뒤에야 듣기 시작해서 놓침. `open_listener`/
  `await_verdict`로 분리해서 SSH 트리거 *전에* 먼저 바인딩하도록 순서를 바꿔 해결.
- **시행착오 2**: 고친 뒤 재실행했더니 여전히 실패 — 이번엔 내 테스트 자체의 기대값 오류
  (`result['result']=='trial'`이라고 썼는데 실제로는 성공 시 `'committed'`). 테스트 수정.
- `cluster.py`: `ClusterDevice`에 `verifier_can_channel`/`verifier_can_secret_path` 추가.
  SSH는 트리거로만 쓰고(그 결과는 `ssh_diagnostic_passed`로만 남김), 실제 `passed` 판정은
  CAN 인증 프레임에서만 가져오도록 교체.
- `qt_verify.py`: `remote-gate`/`--remote` 핸들러에 `--nonce` 추가, Pi3 쪽에서
  `send_verdict()` 호출하도록 연결. `qt-verifier-peers.example.json`에 `can_channel`/
  `can_secret_file` 필드 추가.
- 로컬 테스트용으로 `vcan0` 가상 CAN 인터페이스를 `sudo`로 생성(보드 아님, 노트북 로컬).
- 새 테스트 8개(`tests/test_can_verdict.py`, `tests/test_cluster_can_verdict.py`): 정상
  왕복, 위조 비밀키 차단, 오래된/다른 nonce 무시, SSH가 `passed:true`라고 속였지만 CAN
  검증은 안 보낸 공격 차단까지 확인. 전체 스위트 274개(기존 266 + 신규 8) 통과.
- **실보드(Pi2/Pi3) 배포는 아직 안 함** — 사전 공유 비밀키를 두 보드에 안전하게 배치하는
  절차가 먼저 필요. README에 명시.
