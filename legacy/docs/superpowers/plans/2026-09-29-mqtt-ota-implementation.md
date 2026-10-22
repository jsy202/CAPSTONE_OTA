# MQTT 기반 Qt DigitalDash OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동일 Wi-Fi의 Linux 노트북에서 Raspberry Pi 4B의 Volvo DigitalDash 애플리케이션을 서명 검증, 원자적 활성화 및 자동 롤백과 함께 배포한다.

**Architecture:** 노트북은 MQTT/TLS로 제어 메시지를 발행하고 HTTPS로 서명된 릴리스 파일을 제공한다. Raspberry Pi의 Python OTA 에이전트는 명령과 artifact를 검증해 버전별 디렉터리에 설치하고 `current`/`previous` 링크를 전환한 뒤 systemd 헬스체크 결과에 따라 성공 처리하거나 롤백한다.

**Tech Stack:** Python 3, pytest, paho-mqtt, cryptography/Ed25519, Mosquitto, HTTPS, systemd, Qt/C++/QML, Raspberry Pi 4B

**Spec:** `docs/superpowers/specs/2026-09-29-mqtt-ota-design.md`

## Global Constraints

- Raspberry Pi 4B에는 Linux, systemd, Python 3, OpenSSL 및 Qt 런타임이 설치되어 있다.
- 노트북과 Raspberry Pi는 동일한 신뢰 가능한 Wi-Fi/LAN에 연결된다.
- OTA 대상은 미리 빌드된 ARM용 DigitalDash 실행파일, QML 및 런타임 리소스다.
- Raspberry Pi에서 업데이트 소스를 컴파일하지 않는다.
- MQTT는 제어와 상태 보고에만 사용하고 artifact는 HTTPS로 전송한다.
- 개인 서명키는 노트북에만 두고 Pi에는 검증용 공개키만 설치한다.
- OS, 부트로더, 커널 및 다른 ECU 펌웨어 업데이트는 범위에서 제외한다.
- Volvo240-DigitalDash에서 가져온 코드는 원본 MIT 저작권과 라이선스를 유지한다.
- 인증서 검증을 끄거나 서명 검증을 우회하는 실행 옵션을 제공하지 않는다.

## Review Focus

- 만료됐거나 현재 버전 이하인 정상 서명 manifest는 설치하지 않고 명시적 오류 상태를 발행해야 한다. Task 1과 Task 6에서 검증한다.
- 압축 폭탄, 경로 탈출, 심볼릭 링크 및 특수 파일은 활성 릴리스에 영향을 주기 전에 거부해야 한다. Task 3에서 검증한다.
- 다운로드 또는 프로세스가 중단돼도 기존 `current` 링크와 정상 릴리스는 보존돼야 한다. Task 3과 Task 4에서 검증한다.
- 동일 QoS 1 메시지가 재전송되면 설치를 반복하지 않고 저장된 최종 결과를 다시 발행해야 한다. Task 6에서 검증한다.
- 새 계기판 서비스가 헬스체크를 통과하지 못하면 `previous`가 복구되고 롤백 결과가 영속화돼야 한다. Task 4와 Task 6에서 검증한다.

---

## File Structure

```text
capstone_ota/
├── __init__.py
├── common/
│   ├── errors.py           # 안정적인 오류 코드와 OTA 예외
│   ├── manifest.py         # canonical JSON, SemVer 및 정책 검증
│   └── signing.py          # Ed25519 서명·검증
├── publisher/
│   ├── bundle.py           # payload 패키징과 manifest 생성
│   ├── cli.py              # package, serve, publish 명령
│   ├── http_server.py      # TLS artifact 서버
│   └── mqtt.py             # 업데이트 명령 발행과 상태 관찰
└── agent/
    ├── archive.py          # 제한된 안전 압축 해제
    ├── config.py           # Pi JSON 설정 로드
    ├── downloader.py       # CA 검증 HTTPS 다운로드
    ├── installer.py        # staging, 링크 전환 및 rollback
    ├── service_manager.py  # systemd 어댑터
    ├── state.py            # 원자적 영속 상태
    ├── updater.py          # 한 OTA 작업의 오케스트레이션
    └── cli.py              # MQTT 에이전트 진입점

ota/
├── broker/                 # Mosquitto 설정·ACL 템플릿
├── config/                 # laptop/Pi 설정 예제
├── scripts/                # 인증서와 설치 스크립트
└── systemd/                # Pi 서비스 유닛

dashboard/volvo-digital-dash/
├── upstream/               # 선택적으로 가져온 Qt 앱 소스
├── LICENSE.upstream
└── UPSTREAM.md

tests/
├── unit/
├── integration/
└── fixtures/
```

---

### Task 1: Manifest 계약과 정책 검증

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `capstone_ota/__init__.py`
- Create: `capstone_ota/common/__init__.py`
- Create: `capstone_ota/common/errors.py`
- Create: `capstone_ota/common/manifest.py`
- Test: `tests/unit/test_manifest.py`

**Interfaces:**
- Produces: `ReleaseManifest.from_bytes(raw: bytes) -> ReleaseManifest`
- Produces: `ReleaseManifest.canonical_bytes() -> bytes`
- Produces: `ReleaseManifest.validate_for(device_id: str, current_version: str | None, now: datetime) -> None`
- Produces: `OtaError(code: str, message: str)` and stable codes `INVALID_MANIFEST`, `WRONG_DEVICE`, `EXPIRED`, `ROLLBACK_REJECTED`

- [ ] **Step 1: Write failing manifest parsing and canonicalization tests**

  Assert required fields parse to immutable values, key order does not change `canonical_bytes()`, duplicate JSON keys and unknown schema versions raise `INVALID_MANIFEST`, and invalid SHA-256, URL, size, entrypoint or RFC 3339 timestamps are rejected.

- [ ] **Step 2: Run the focused tests and confirm failure**

  Run: `python3 -m pytest tests/unit/test_manifest.py -q`

  Expected: FAIL because `capstone_ota.common.manifest` does not exist.

- [ ] **Step 3: Implement the manifest value object and canonical JSON**

  Implement `ReleaseManifest` with the exact fields in the spec. Permit only HTTPS artifact URLs, positive sizes, lowercase 64-character SHA-256 values, relative normalized entrypoints and SemVer versions.

- [ ] **Step 4: Write failing policy tests**

  Add assertions for correct device acceptance, wrong device rejection, expired manifest rejection, current-or-older version rejection and first-install behavior when `current_version is None`.

- [ ] **Step 5: Implement `validate_for` and stable error codes**

  Compare timezone-aware UTC timestamps and SemVer values; do not add a downgrade override.

- [ ] **Step 6: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_manifest.py -q`

  Expected: PASS.

  Commit: `feat: define signed release manifest contract`

---

### Task 2: Ed25519 서명과 릴리스 패키징

**Files:**
- Create: `capstone_ota/common/signing.py`
- Create: `capstone_ota/publisher/__init__.py`
- Create: `capstone_ota/publisher/bundle.py`
- Test: `tests/unit/test_signing.py`
- Test: `tests/unit/test_bundle.py`

**Interfaces:**
- Consumes: `ReleaseManifest.canonical_bytes()` from Task 1
- Produces: `generate_key_pair(private_path: Path, public_path: Path) -> None`
- Produces: `sign_manifest(manifest: ReleaseManifest, private_key_path: Path) -> bytes`
- Produces: `verify_manifest_signature(manifest: ReleaseManifest, signature: bytes, public_key_path: Path) -> None`
- Produces: `build_release(payload_dir: Path, output_dir: Path, request: BundleRequest) -> ReleaseBundle`
- Produces: `ReleaseBundle(archive_path: Path, manifest_path: Path, signature_path: Path)`

- [ ] **Step 1: Write failing Ed25519 tests**

  Assert a generated key pair verifies a signature; modified manifest bytes, modified signature and a different public key raise `SIGNATURE_INVALID`; private-key files are created with mode `0600`.

- [ ] **Step 2: Run the signing tests and confirm failure**

  Run: `python3 -m pytest tests/unit/test_signing.py -q`

  Expected: FAIL because the signing API is absent.

- [ ] **Step 3: Implement signing using `cryptography` Ed25519 primitives**

  Load PEM keys, sign only `canonical_bytes()`, map all verification failures to `SIGNATURE_INVALID`, and never log private-key contents.

- [ ] **Step 4: Write failing deterministic bundle tests**

  Assert the archive contains only payload-relative regular files and directories, its SHA-256 and size match the manifest, the three output filenames use the version, and an absent or non-executable entrypoint is rejected.

- [ ] **Step 5: Implement deterministic packaging and manifest emission**

  Normalize tar metadata needed for repeatable archives, write manifest bytes canonically, sign after all artifact fields are final, and use temporary files followed by `os.replace`.

- [ ] **Step 6: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_signing.py tests/unit/test_bundle.py -q`

  Expected: PASS.

  Commit: `feat: package and sign OTA releases`

---

### Task 3: 안전한 다운로드, 압축 해제와 상태 저장

**Files:**
- Create: `capstone_ota/agent/__init__.py`
- Create: `capstone_ota/agent/downloader.py`
- Create: `capstone_ota/agent/archive.py`
- Create: `capstone_ota/agent/state.py`
- Test: `tests/unit/test_downloader.py`
- Test: `tests/unit/test_archive.py`
- Test: `tests/unit/test_state.py`

**Interfaces:**
- Consumes: `ReleaseManifest` and `OtaError` from Task 1
- Produces: `download_https(url: str, destination: Path, ca_file: Path, expected_size: int, max_bytes: int) -> DownloadResult`
- Produces: `safe_extract_tar(archive: Path, destination: Path, limits: ArchiveLimits) -> None`
- Produces: `OtaState.load(path: Path) -> OtaState`
- Produces: `OtaState.save_atomic(path: Path) -> None`
- Produces: state fields `current_version`, `previous_version`, `completed_jobs`, `failed_versions`, `active_job`

- [ ] **Step 1: Write failing downloader tests**

  Use a temporary TLS HTTP server and test valid CA download, untrusted certificate rejection, declared-size mismatch, maximum-size enforcement and interrupted transfer cleanup. Assert `.part` files are never returned as completed artifacts.

- [ ] **Step 2: Implement bounded HTTPS download**

  Use an SSL context created from `ca_file`, stream fixed-size chunks, reject non-HTTPS URLs, enforce `Content-Length` when present and count bytes independently.

- [ ] **Step 3: Write failing malicious archive tests**

  Cover absolute paths, `..`, symlinks, hard links, device/FIFO members, too many files, excessive expanded size and an ordinary nested payload.

- [ ] **Step 4: Implement `safe_extract_tar`**

  Validate the complete member list and limits before writing any member, then extract only directories and regular files beneath the resolved destination.

- [ ] **Step 5: Write failing atomic state tests**

  Assert missing state returns defaults, a saved state round-trips, malformed state fails closed, and an injected `os.replace` failure leaves the previous file readable.

- [ ] **Step 6: Implement state validation and atomic persistence**

  Bound `completed_jobs` history, fsync the temporary file before replacement and reject unknown schema versions.

- [ ] **Step 7: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_downloader.py tests/unit/test_archive.py tests/unit/test_state.py -q`

  Expected: PASS.

  Commit: `feat: add safe OTA download and staging primitives`

---

### Task 4: 릴리스 활성화와 systemd 롤백

**Files:**
- Create: `capstone_ota/agent/service_manager.py`
- Create: `capstone_ota/agent/installer.py`
- Test: `tests/unit/test_installer.py`
- Test: `tests/unit/test_service_manager.py`

**Interfaces:**
- Consumes: extracted staging directory and `OtaState` from Task 3
- Produces: protocol `ServiceManager.restart_and_wait_healthy(unit: str, timeout_seconds: int) -> bool`
- Produces: `SystemdServiceManager`
- Produces: `ReleaseInstaller.install_staged(version: str, staging_dir: Path, entrypoint: str) -> Path`
- Produces: `ReleaseInstaller.activate(version: str) -> ActivationResult`
- Produces: `ReleaseInstaller.rollback() -> ActivationResult`

- [ ] **Step 1: Write failing installer filesystem tests**

  In a temporary install root, assert staged install rejects missing/non-executable entrypoints, successful activation atomically updates `previous` then `current`, and an injected link replacement failure keeps the old `current` resolvable.

- [ ] **Step 2: Implement staging promotion and atomic relative symlinks**

  Require releases and staging on the same filesystem, reject an existing version with different content, and never delete the release referenced by `current` or `previous`.

- [ ] **Step 3: Write failing health and rollback tests**

  With a fake `ServiceManager`, assert healthy activation retains the new link; unhealthy activation restores the previous link, restarts it and reports `rolled_back=True`; rollback without a previous release fails without changing `current`.

- [ ] **Step 4: Implement activation transaction and systemd adapter**

  Invoke only fixed `systemctl restart digital-dash.service` and `systemctl is-active --quiet digital-dash.service` commands, enforce the 15-second health window and keep command arguments non-configurable from MQTT input.

- [ ] **Step 5: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_installer.py tests/unit/test_service_manager.py -q`

  Expected: PASS.

  Commit: `feat: activate releases with automatic rollback`

---

### Task 5: 노트북 HTTPS 서버와 MQTT 배포 CLI

**Files:**
- Create: `capstone_ota/publisher/http_server.py`
- Create: `capstone_ota/publisher/mqtt.py`
- Create: `capstone_ota/publisher/cli.py`
- Test: `tests/unit/test_publisher_cli.py`
- Test: `tests/unit/test_publisher_mqtt.py`
- Test: `tests/integration/test_https_artifact_server.py`

**Interfaces:**
- Consumes: `build_release` from Task 2
- Produces: console script `capstone-ota-publish`
- Produces: commands `keys`, `package`, `serve`, `publish`, `watch`
- Produces: `build_command(bundle: ReleaseBundle, base_url: str) -> dict[str, object]`
- Produces: `PublisherMqtt.publish_command(device_id: str, command: Mapping[str, object]) -> None`

- [ ] **Step 1: Write failing CLI contract tests**

  Assert every command validates required files, `package` emits the three bundle paths, `serve` requires a certificate and key, and `publish` rejects a base URL that is not HTTPS.

- [ ] **Step 2: Implement argparse commands with dependency-injected handlers**

  Keep private-key and broker-password values out of normal output and error messages.

- [ ] **Step 3: Write failing MQTT tests with a fake paho client**

  Assert TLS CA/client certificate configuration, topic `capstone/<device>/ota/command`, QoS 1, retained publish, acknowledgement timeout and device ID validation.

- [ ] **Step 4: Implement publisher MQTT wrapper and status watch**

  Subscribe only to the selected device status topic and render stage, progress, version and errors without accepting commands from status data.

- [ ] **Step 5: Write and satisfy the HTTPS server integration test**

  Start the server on an ephemeral port, download with the configured CA, reject traversal URLs and verify only the configured release root is exposed.

- [ ] **Step 6: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_publisher_cli.py tests/unit/test_publisher_mqtt.py tests/integration/test_https_artifact_server.py -q`

  Expected: PASS.

  Commit: `feat: add secure laptop release publisher`

---

### Task 6: Raspberry Pi MQTT OTA 에이전트

**Files:**
- Create: `capstone_ota/agent/config.py`
- Create: `capstone_ota/agent/updater.py`
- Create: `capstone_ota/agent/cli.py`
- Test: `tests/unit/test_agent_config.py`
- Test: `tests/unit/test_updater.py`
- Test: `tests/integration/test_agent_message_flow.py`

**Interfaces:**
- Consumes: manifest, signing, download, state and installer interfaces from Tasks 1-4
- Produces: `AgentConfig.from_json(path: Path) -> AgentConfig`
- Produces: `UpdateAgent.handle_command(payload: bytes) -> UpdateResult`
- Produces: `UpdateAgent.publish_status(stage: str, progress: int, error: OtaError | None = None) -> None`
- Produces: console script `capstone-ota-agent`

- [ ] **Step 1: Write failing strict configuration tests**

  Require device ID, broker host/port, CA and client certificate paths, public key, install/state roots and download/archive limits. Reject unknown keys, missing files, relative system paths and non-TLS MQTT ports unless the test fixture explicitly injects a transport.

- [ ] **Step 2: Implement immutable agent configuration**

  Configuration controls local paths and limits only; MQTT messages must not be able to replace service names, trusted keys, CA files or installation roots.

- [ ] **Step 3: Write failing update orchestration tests**

  Assert exact stage order for success, signature-before-artifact-install, hash and size verification, expired/current-or-older rejection, wrong-device rejection, duplicate job idempotency, state persistence and unhealthy-service rollback reporting.

- [ ] **Step 4: Implement `UpdateAgent.handle_command`**

  Download manifest and signature first, validate and verify them, reserve the active job, download/check/extract/install/activate, then record and publish the terminal result. On any error leave `current` unchanged or invoke rollback if activation already occurred.

- [ ] **Step 5: Write failing MQTT message-flow integration test**

  With an in-process fake transport, deliver a command twice and assert one install, two identical terminal status reports and no second download. Deliver an older signed release and assert `ROLLBACK_REJECTED`.

- [ ] **Step 6: Implement the paho event loop and graceful shutdown**

  Subscribe at QoS 1, process only the exact device command topic on a single update worker, reject concurrent jobs as `BUSY`, reconnect with backoff and keep the dashboard independent of broker availability.

- [ ] **Step 7: Run tests and commit**

  Run: `python3 -m pytest tests/unit/test_agent_config.py tests/unit/test_updater.py tests/integration/test_agent_message_flow.py -q`

  Expected: PASS.

  Commit: `feat: implement Raspberry Pi MQTT OTA agent`

---

### Task 7: Broker, 인증서, systemd 및 설치 자동화

**Files:**
- Create: `ota/broker/mosquitto.conf`
- Create: `ota/broker/acl.template`
- Create: `ota/config/laptop.example.json`
- Create: `ota/config/pi.example.json`
- Create: `ota/systemd/capstone-ota-agent.service`
- Create: `ota/systemd/digital-dash.service`
- Create: `ota/scripts/generate-dev-pki.sh`
- Create: `ota/scripts/install-laptop.sh`
- Create: `ota/scripts/install-pi.sh`
- Test: `tests/integration/test_config_assets.py`

**Interfaces:**
- Consumes: console entrypoints from Tasks 5 and 6
- Produces: `generate-dev-pki.sh <hostname> <device-id> <output-dir>`
- Produces: `install-laptop.sh <config-path>` and `install-pi.sh <config-path>`
- Produces: system users `capstone-ota` and `digital-dash`

- [ ] **Step 1: Write failing infrastructure asset tests**

  Parse templates and assert MQTT listener 8883 requires TLS, anonymous access is disabled, ACL contains publisher and per-device rules, systemd units use fixed absolute paths, the dashboard user lacks write access to releases, and no sample private key/password is committed.

- [ ] **Step 2: Implement Mosquitto and configuration templates**

  Document placeholder substitution, bind to the LAN listener, require client certificates or named credentials, and separate publisher/Pi ACLs.

- [ ] **Step 3: Implement PKI generation script**

  Generate a development CA, HTTPS server certificate with hostname/IP SANs, MQTT broker certificate, publisher client certificate, per-device client certificate and Ed25519 update key pair. Use restrictive modes and refuse to overwrite an existing private key without an explicit confirmation flag.

- [ ] **Step 4: Implement idempotent installation scripts and systemd units**

  Create users/directories with least privilege, install only public trust material on Pi, render configs, reload systemd and leave services disabled until configuration validation succeeds.

- [ ] **Step 5: Run checks and commit**

  Run: `python3 -m pytest tests/integration/test_config_assets.py -q`

  Run: `bash -n ota/scripts/*.sh`

  Expected: all PASS with no shell syntax errors.

  Commit: `feat: provision secure OTA services`

---

### Task 8: Volvo DigitalDash 소스 연결과 릴리스 예제

**Files:**
- Create: `dashboard/volvo-digital-dash/UPSTREAM.md`
- Create: `dashboard/volvo-digital-dash/LICENSE.upstream`
- Create: `dashboard/volvo-digital-dash/import-upstream.sh`
- Create: `dashboard/volvo-digital-dash/make-payload.sh`
- Create: `tests/fixtures/dashboard-payload/bin/digital-dash`
- Test: `tests/integration/test_dashboard_payload.py`

**Interfaces:**
- Consumes: upstream repository `whitfijs-jw/Volvo240-DigitalDash` commit `793452919127065536bcb7a08f98838fa963d75e`
- Produces: `import-upstream.sh` importing only `QtDash/VolvoDigitalDashModels` plus required app assets
- Produces: `make-payload.sh <built-binary> <output-dir>` producing `bin/digital-dash` layout accepted by Task 2

- [ ] **Step 1: Write failing payload layout test**

  Assert the sample and generated payload contain an executable `bin/digital-dash`, no host build paths, and only files allowed by the bundle builder.

- [ ] **Step 2: Add provenance, MIT license and pinned import script**

  Import only the Qt application subtree rather than upstream Buildroot or hardware CAD data. Record repository URL, pinned commit, imported paths and local modifications in `UPSTREAM.md`.

- [ ] **Step 3: Implement payload assembly script**

  Accept an already-built ARM binary, copy optional runtime resources, set executable mode and print the exact `capstone-ota-publish package` command for the result.

- [ ] **Step 4: Run tests and commit**

  Run: `python3 -m pytest tests/integration/test_dashboard_payload.py -q`

  Expected: PASS.

  Commit: `feat: integrate Volvo DigitalDash release payload`

---

### Task 9: End-to-end 테스트와 사용자 문서

**Files:**
- Create: `tests/integration/test_end_to_end_update.py`
- Create: `README.md`
- Create: `docs/LAPTOP_SETUP.md`
- Create: `docs/RASPBERRY_PI_SETUP.md`
- Create: `docs/DEPLOYMENT_GUIDE.md`
- Create: `docs/RECOVERY_GUIDE.md`
- Create: `docs/SECURITY.md`
- Create: `.gitignore`

**Interfaces:**
- Consumes: all prior public interfaces and scripts
- Produces: one documented laptop-to-Pi deployment workflow and one local simulation workflow

- [ ] **Step 1: Write failing end-to-end success and rollback tests**

  Use temporary install roots, generated TLS/signing keys, a local HTTPS server and fake systemd service. Prove 1.0.0→1.0.1 activation, modified artifact rejection without link changes and intentionally unhealthy 1.0.2 rollback to 1.0.1.

- [ ] **Step 2: Complete missing integration behavior until the tests pass**

  Do not weaken TLS, signature, expiry, version or archive checks to simplify the harness.

- [ ] **Step 3: Write the root README**

  Include scope, architecture diagram, repository map, prerequisites, five-minute local demo, real Pi flow, MQTT topics/statuses, security model, rollback demonstration, Volvo attribution and the explicit statement that the project is not Uptane-compliant production vehicle firmware OTA.

- [ ] **Step 4: Write operational guides**

  `LAPTOP_SETUP.md` covers dependencies, keys, Mosquitto and HTTPS. `RASPBERRY_PI_SETUP.md` covers users, directories, config and systemd. `DEPLOYMENT_GUIDE.md` covers package/publish/watch. `RECOVERY_GUIDE.md` covers logs, manual link recovery and service restart. `SECURITY.md` covers assets, trust boundaries, attacks addressed, key rotation limitations and production gaps.

- [ ] **Step 5: Run complete verification**

  Run: `python3 -m pytest -q`

  Run: `python3 -m compileall -q capstone_ota`

  Run: `bash -n ota/scripts/*.sh dashboard/volvo-digital-dash/*.sh`

  Run: `git diff --check`

  Expected: all tests pass, compilation and shell checks exit 0, and no whitespace errors appear.

- [ ] **Step 6: Confirm no secrets or generated artifacts are tracked**

  Run: `git status --short && git ls-files | rg '(\.key$|private|password|\.part$|\.tar\.gz$)'`

  Expected: only intentionally named documentation/config templates match; no generated private keys, passwords, partial downloads or release archives are tracked.

- [ ] **Step 7: Commit**

  Commit: `docs: add OTA setup deployment and recovery guides`

---

### Task 10: Raspberry Pi 하드웨어 검증 체크리스트

**Files:**
- Create: `docs/RPI_VALIDATION_CHECKLIST.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: deployed services from Tasks 7-9
- Produces: a repeatable manual acceptance record for one Raspberry Pi 4B

- [ ] **Step 1: Write the hardware acceptance checklist**

  Include OS/architecture, available disk, clock sync, certificate hostname, MQTT reconnect, valid update, tampered update, Wi-Fi interruption, agent restart, application crash rollback, reboot persistence and journal collection with pass/fail/result fields.

- [ ] **Step 2: Link the checklist from README and record unexecuted hardware scope honestly**

  Mark hardware-only checks pending until a Pi is available; do not claim hardware validation from local mocks.

- [ ] **Step 3: Run final repository checks and commit**

  Run: `python3 -m pytest -q && git diff --check && git status --short`

  Expected: tests pass and only the checklist/README edits remain before commit.

  Commit: `docs: add Raspberry Pi OTA validation checklist`
