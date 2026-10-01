# Zonal Multi-ECU OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-Raspberry-Pi zonal OTA demonstrator that stages signed Central and Cluster applications, validates their declared and observed CAN compatibility, commits both together, or rolls both back to verified stable application slots.

**Architecture:** The laptop publishes a signed vehicle bundle to a stable coordinator on the Central HPC. The coordinator caches both releases, stages the local Central application, instructs the Cluster agent over Classic CAN, serves the Cluster release over private Ethernet/HTTPS, and owns the vehicle transaction state. Both update targets use application A/B slots; commit is allowed only after static dependency, heartbeat, CAN contract, and functional checks pass.

**Tech Stack:** Python 3.10+, dataclasses, `cryptography` Ed25519, `paho-mqtt`, standard-library HTTPS and Linux SocketCAN, pytest, systemd, Raspberry Pi OS, MCP2515 CAN HATs.

**Spec:** `docs/superpowers/specs/2026-10-01-zonal-multi-ecu-ota-design.md`

## Global Constraints

- Preserve the existing single-device `ReleaseManifest` schema and commands for backward compatibility.
- Update applications only; never update Raspberry Pi OS, the kernel, bootloader, coordinator, or update agents.
- Use Classic CAN 2.0 with 11-bit IDs, eight-byte payloads, and a configured bitrate of 500 kbit/s.
- Transfer archives and detailed metadata over certificate-verified HTTPS, never over CAN.
- Accept only a two-target initial bundle containing exactly `central-control` and `digital-cluster`.
- Use whole-bundle rollback for every activation or post-activation verification failure.
- A successful rollback requires a successful recovery verification; otherwise report `RECOVERY_FAILED`.
- Persist state atomically before every externally visible transaction transition.
- Keep the coordinator and update agents running from stable, non-updated paths.

## Review Focus

- Duplicate, missing, or unexpected ECU targets must make bundle parsing fail before any download or staging; Task 1 pins this behavior.
- A 32-bit CAN transaction-token collision with active or retained history must be rejected rather than mapped to the wrong bundle; Task 4 pins this behavior.
- Restart after one ECU activates but before the other reports active must enter whole-bundle rollback; Tasks 3 and 5 pin this behavior.
- Stale, repeated, wrong-transaction, or CRC-corrupt CAN frames must not advance the transaction; Tasks 2 and 4 pin this behavior.
- If application rollback completes on both ECUs but prior-version CAN/function verification fails, the terminal state must be `RECOVERY_FAILED`, never `ROLLED_BACK`; Task 5 pins this behavior.

---

## File Structure

- `capstone_ota/common/vehicle_bundle.py`: strict signed vehicle-bundle model and dependency validation.
- `capstone_ota/common/signing.py`: generic canonical-document signing while retaining existing manifest APIs.
- `capstone_ota/common/can_protocol.py`: eight-byte CAN codecs, enums, CRC-8, and CAN IDs.
- `capstone_ota/common/socketcan.py`: small Linux SocketCAN adapter behind a testable transport protocol.
- `capstone_ota/agent/slots.py`: application A/B slot state, staging, trial activation, commit, rollback, and restart recovery.
- `capstone_ota/agent/zonal.py`: idempotent Cluster command handler using HTTPS for content and CAN for commands/status.
- `capstone_ota/coordinator/state.py`: atomic vehicle transaction persistence.
- `capstone_ota/coordinator/compatibility.py`: declared dependency and observed heartbeat/function checks.
- `capstone_ota/coordinator/core.py`: prepare/activate/verify/commit/rollback orchestration.
- `capstone_ota/coordinator/cli.py`: MQTT, HTTPS cache, SocketCAN, local-agent, and coordinator runtime wiring.
- `capstone_ota/publisher/vehicle_bundle.py`: deterministic signed vehicle-bundle output.
- `capstone_ota/publisher/cli.py`: `vehicle-package` and `vehicle-publish` commands without changing legacy commands.
- `ota/config/*.example.json`, `ota/systemd/*.service`, and setup/deployment docs: two-Pi installation assets.

### Task 1: Signed Vehicle Bundle and Dependency Validation

**Files:**
- Create: `capstone_ota/common/vehicle_bundle.py`
- Create: `capstone_ota/publisher/vehicle_bundle.py`
- Modify: `capstone_ota/common/signing.py`
- Test: `tests/unit/test_vehicle_bundle.py`
- Test: `tests/unit/test_signing.py`

**Interfaces:**
- Consumes: existing `ReleaseManifest.canonical_bytes()` and Ed25519 key files.
- Produces: `VehicleBundleManifest.from_bytes(raw: bytes)`, `canonical_bytes()`, `validate_at(now: datetime)`, `validate_dependencies()`, `transaction_token: int`, `EcuTarget`, `DependencyRule`, `HealthPolicy`, and `build_vehicle_bundle(output_dir: Path, manifest: VehicleBundleManifest, private_key_path: Path, retained_tokens: Collection[int] = ()) -> VehicleBundleArtifacts`.

- [ ] **Step 1: Write failing strict-schema and canonicalization tests**

  Test exact two-target parsing, immutable canonical output, duplicate JSON keys, duplicate ECU IDs, missing required roles, extra roles, invalid UUID/SemVer/timestamps/URLs/hashes, and a transaction token collision supplied to the publisher builder.

- [ ] **Step 2: Run the focused tests and verify they fail because the bundle types do not exist**

  Run: `python -m pytest tests/unit/test_vehicle_bundle.py -q`

- [ ] **Step 3: Implement the bundle dataclasses and strict parser**

  Use a wrapper target containing `ecu_id`, `hardware_id`, `software_version`, `can_interface`, protocol major/minor, capabilities, and signed release manifest/signature URLs. Derive the 32-bit token from the first four bytes of the UUID and expose collision checking in the publisher builder.

- [ ] **Step 4: Add failing dependency-policy tests**

  Pin required ECU presence, exact CAN major, minimum minor, hardware ID, version range, and capability behavior. Assert every error has a stable `OtaError.code`.

- [ ] **Step 5: Implement `validate_dependencies()` and generic canonical signing**

  Add a `CanonicalDocument` protocol in `signing.py`; keep `sign_manifest` and `verify_manifest_signature` as compatibility wrappers and add `sign_document`/`verify_document_signature`.

- [ ] **Step 6: Implement deterministic bundle artifact generation and run tests**

  Run: `python -m pytest tests/unit/test_vehicle_bundle.py tests/unit/test_signing.py -q`
  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit message: `feat: add signed vehicle bundle metadata`

### Task 2: Classic CAN Wire Protocol and SocketCAN Adapter

**Files:**
- Create: `capstone_ota/common/can_protocol.py`
- Create: `capstone_ota/common/socketcan.py`
- Test: `tests/unit/test_can_protocol.py`
- Test: `tests/unit/test_socketcan.py`

**Interfaces:**
- Consumes: unsigned integers and semantic version triples from Task 1.
- Produces: `CanFrame(can_id: int, data: bytes)`, `OtaCommandFrame`, `OtaStatusFrame`, `HeartbeatFrame`, `VehicleStatusFrame`, `FunctionalTestRequestFrame`, `FunctionalTestResultFrame`, each with `encode() -> CanFrame` and `decode(frame)`. Produces `CanTransport.send(frame)` and `recv(timeout) -> CanFrame | None`, plus `SocketCanTransport(interface: str)`.

- [ ] **Step 1: Write failing round-trip tests for every frame type**

  Assert exact IDs `0x100`, `0x101`, `0x200`, `0x600`, `0x601`, `0x610`, and `0x611`, exact eight-byte payloads, numeric ranges, endian order, command/status enums, and CRC-8 vectors.

- [ ] **Step 2: Write failing rejection tests**

  Cover wrong ID, payload length other than eight, corrupt CRC, unknown enum, out-of-range fields, stale rolling counter helper behavior, and wrong transaction token.

- [ ] **Step 3: Implement the pure codecs and CRC-8**

  Use `struct` and a single documented CRC-8 polynomial. Keep all platform I/O outside this module.

- [ ] **Step 4: Implement the SocketCAN adapter behind the transport protocol**

  Use Python's standard `socket` module with `AF_CAN`, `SOCK_RAW`, and `CAN_RAW`; defer opening until `open()` so codec tests remain portable on Windows.

- [ ] **Step 5: Run focused tests**

  Run: `python -m pytest tests/unit/test_can_protocol.py tests/unit/test_socketcan.py -q`
  Expected: all pass without requiring CAN hardware.

- [ ] **Step 6: Commit**

  Commit message: `feat: add Classic CAN OTA protocol`

### Task 3: Application A/B Slot Lifecycle

**Files:**
- Create: `capstone_ota/agent/slots.py`
- Modify: `capstone_ota/agent/service_manager.py`
- Test: `tests/unit/test_slots.py`
- Test: `tests/unit/test_service_manager.py`

**Interfaces:**
- Consumes: a safely extracted application directory, entry point, version, transaction UUID, and a `ServiceManager`.
- Produces: `SlotState.load/save_atomic`, `ABSlotInstaller.stage(...)`, `activate_trial(transaction_id)`, `commit(transaction_id)`, `rollback(transaction_id)`, and `recover_on_startup()`. `SystemdServiceManager` accepts an explicit immutable allowed-unit set.

- [ ] **Step 1: Write failing slot initialization and staging tests**

  Pin A-as-stable initialization, staging only into the inactive slot, executable entry-point validation, content replacement safety, and rejection of a second transaction while a trial exists.

- [ ] **Step 2: Implement atomic slot metadata and inactive-slot staging**

  Persist state with file and parent-directory `fsync`. Replace slot content only after a complete staged tree is ready on the same filesystem.

- [ ] **Step 3: Write failing trial/commit/rollback tests**

  Assert service restart, stable slot unchanged during trial, commit promotion, rollback restoration, idempotent repeated commands, and wrong-transaction rejection.

- [ ] **Step 4: Implement trial, commit, and rollback**

  Keep the current `ReleaseInstaller` intact for legacy OTA; the new zonal flow uses `ABSlotInstaller`.

- [ ] **Step 5: Write and implement restart recovery tests**

  Cover restart in `staging`, `trial`, `committing`, and `rolling_back`; any uncommitted active trial returns to the stable slot. Cover the case where activation happened but state persistence was interrupted.

- [ ] **Step 6: Generalize the systemd manager's unit allowlist and run tests**

  Run: `python -m pytest tests/unit/test_slots.py tests/unit/test_service_manager.py tests/unit/test_installer.py -q`
  Expected: all new and legacy tests pass.

- [ ] **Step 7: Commit**

  Commit message: `feat: add application A-B slot lifecycle`

### Task 4: Cluster Zonal Update Agent

**Files:**
- Create: `capstone_ota/agent/zonal.py`
- Create: `capstone_ota/agent/zonal_config.py`
- Test: `tests/unit/test_zonal_agent.py`
- Test: `tests/unit/test_zonal_config.py`

**Interfaces:**
- Consumes: `CanTransport`, Task 2 frames, `ABSlotInstaller`, Central HTTPS base URL, trusted CA/public key, and archive limits.
- Produces: `ZonalAgent.handle_command(frame) -> OtaStatusFrame`, `poll_once(timeout)`, `publish_heartbeat()`, and strict `ZonalAgentConfig.from_json(path)`.

- [ ] **Step 1: Write failing strict configuration tests**

  Pin device/role IDs, `can0`, 500000 bitrate, `https://10.10.0.1:8443`, absolute trusted paths, install root, state file, service unit, archive limits, and rejection of unknown fields.

- [ ] **Step 2: Implement zonal configuration parsing**

- [ ] **Step 3: Write failing command lifecycle tests with fake HTTPS and CAN**

  Cover PREPARE download/verification/stage, ACTIVATE trial, COMMIT, ROLLBACK, QUERY_STATUS, repeated commands, wrong token, stale command, corrupt frame rejection, and rejection of a token collision with retained history.

- [ ] **Step 4: Implement `ZonalAgent` command handling**

  Retrieve transaction metadata from `/transactions/<token>/cluster/`, independently verify release signature/hash, then call Task 3 slot operations. Persist token-to-UUID history before returning `READY`.

- [ ] **Step 5: Write and implement heartbeat and functional-result behavior**

  Heartbeats contain the active/trial flag, numeric application version, protocol version, rolling counter, and CRC. Functional results report the values interpreted by the Cluster application adapter, not merely values echoed from the request.

- [ ] **Step 6: Run focused tests**

  Run: `python -m pytest tests/unit/test_zonal_agent.py tests/unit/test_zonal_config.py -q`
  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit message: `feat: add CAN-controlled cluster update agent`

### Task 5: Central Coordinator, Compatibility Checks, and Recovery

**Files:**
- Create: `capstone_ota/coordinator/__init__.py`
- Create: `capstone_ota/coordinator/state.py`
- Create: `capstone_ota/coordinator/compatibility.py`
- Create: `capstone_ota/coordinator/core.py`
- Test: `tests/unit/test_coordinator_state.py`
- Test: `tests/unit/test_compatibility.py`
- Test: `tests/unit/test_coordinator.py`

**Interfaces:**
- Consumes: verified `VehicleBundleManifest`, local `ABSlotInstaller`, a `ZoneClient` exposing prepare/activate/commit/rollback/status, and a `CompatibilityProbe` exposing heartbeats, CAN observations, and functional results.
- Produces: `VehicleTransactionState.load/save_atomic`, `VehicleCoordinator.execute(bundle, artifacts) -> VehicleUpdateResult`, `recover_on_startup()`, and stable aggregate progress events.

- [ ] **Step 1: Write failing transaction-state tests**

  Pin the exact state graph from the spec, atomic persistence, corrupt/unknown state rejection, retained transaction tokens, and transition rejection outside the graph.

- [ ] **Step 2: Implement transaction persistence and transition validation**

- [ ] **Step 3: Write failing compatibility tests**

  Cover static dependency success/failure, expected heartbeat versions, missed periods, stale counters, wrong CAN ID/DLC/range, functional value equality, and semantic mismatch despite matching declared protocol versions.

- [ ] **Step 4: Implement `CompatibilityValidator`**

  Return structured evidence and stable error codes suitable for persisted logs and MQTT status.

- [ ] **Step 5: Write failing coordinator happy-path and prepare-abort tests**

  Assert both targets are cached and staged before activation, Cluster READY precedes activation, both validations precede commit, and a prepare failure changes no active slot.

- [ ] **Step 6: Implement prepare, activation, verification, and commit orchestration**

- [ ] **Step 7: Write failing whole-bundle rollback and restart tests**

  Cover one target activation failure, runtime semantic failure, heartbeat loss, duplicate command, restart after one activation, restart during rollback, and rollback-complete-but-recovery-check-failed resulting in `RECOVERY_FAILED`.

- [ ] **Step 8: Implement rollback and restart recovery**

  Send remote rollback before restarting the local Central application, but persist `ROLLING_BACK` first. Treat every operation as idempotent.

- [ ] **Step 9: Run focused tests**

  Run: `python -m pytest tests/unit/test_coordinator_state.py tests/unit/test_compatibility.py tests/unit/test_coordinator.py -q`
  Expected: all pass.

- [ ] **Step 10: Commit**

  Commit message: `feat: coordinate atomic vehicle application updates`

### Task 6: Publisher and Runtime Wiring

**Files:**
- Modify: `capstone_ota/publisher/cli.py`
- Create: `capstone_ota/coordinator/config.py`
- Create: `capstone_ota/coordinator/cli.py`
- Create: `capstone_ota/agent/zonal_cli.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_publisher_cli.py`
- Test: `tests/unit/test_coordinator_config.py`
- Test: `tests/unit/test_runtime_wiring.py`

**Interfaces:**
- Consumes: Tasks 1-5 public interfaces and existing MQTT/HTTPS helpers.
- Produces: `capstone-ota-publish vehicle-package`, `capstone-ota-publish vehicle-publish`, `capstone-ota-coordinator`, and `capstone-ota-zone-agent` console entry points.

- [ ] **Step 1: Write failing publisher CLI tests**

  Pin required Central/Cluster manifest and signature arguments, bundle compatibility values, deterministic output names, signed bundle creation, and exact MQTT command URLs.

- [ ] **Step 2: Implement `vehicle-package` and `vehicle-publish` without changing legacy commands**

- [ ] **Step 3: Write failing coordinator configuration tests**

  Require laptop MQTT TLS settings, update signing public key, cache root, private HTTPS bind/certificate, `can0`, Central slot/service settings, transaction state path, and exact ECU identities.

- [ ] **Step 4: Implement runtime configuration and entry points**

  Wire the coordinator to the existing MQTT publisher contract, a directory-backed HTTPS cache, SocketCAN, the local slot installer, and structured status events. Wire the Cluster event loop to SocketCAN and its fixed Central HTTPS base URL.

- [ ] **Step 5: Add shutdown and startup-recovery tests**

  Assert startup runs recovery before accepting commands and shutdown stops MQTT, HTTPS, worker threads, and CAN sockets without dropping persisted state.

- [ ] **Step 6: Run focused and legacy CLI tests**

  Run: `python -m pytest tests/unit/test_publisher_cli.py tests/unit/test_coordinator_config.py tests/unit/test_runtime_wiring.py tests/unit/test_publisher_mqtt.py tests/unit/test_agent_config.py -q`
  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit message: `feat: wire zonal OTA publisher and services`

### Task 7: End-to-End Simulation and Hardware Deployment Assets

**Files:**
- Create: `tests/integration/test_zonal_end_to_end.py`
- Create: `tests/integration/test_zonal_recovery.py`
- Create: `ota/config/coordinator.example.json`
- Create: `ota/config/cluster-zonal.example.json`
- Create: `ota/systemd/capstone-ota-coordinator.service`
- Create: `ota/systemd/capstone-ota-zone-agent.service`
- Create: `docs/ZONAL_OTA_GUIDE.md`
- Modify: `README.md`
- Modify: `docs/DEPLOYMENT_GUIDE.md`
- Modify: `docs/SECURITY.md`

**Interfaces:**
- Consumes: all prior task interfaces.
- Produces: a no-hardware in-memory demonstration, Raspberry Pi configuration assets, and operator steps for Wi-Fi/Ethernet/CAN setup and the six acceptance scenarios.

- [ ] **Step 1: Write an in-memory success integration test**

  Build and sign two releases plus a vehicle bundle, serve artifacts over a real local TLS server, use paired in-memory CAN transports, stage both applications, verify heartbeat/function data, and assert both slots commit.

- [ ] **Step 2: Write runtime incompatibility and whole-bundle rollback test**

  Make the Cluster adapter misinterpret the speed scale while declaring the expected protocol; assert both prior slots are restored and recovery evidence is recorded.

- [ ] **Step 3: Write interrupted-verification recovery test**

  Persist `VERIFYING` with both trial slots active, recreate coordinator/agents, invoke startup recovery, and assert both stable slots and prior CAN behavior return.

- [ ] **Step 4: Run integration and full regression tests**

  Run: `python -m pytest tests/integration/test_zonal_end_to_end.py tests/integration/test_zonal_recovery.py -q`
  Then: `python -m pytest -q`
  Expected: all pass.

- [ ] **Step 5: Add strict example configurations and hardened systemd units**

  Document `wlan0`, direct `eth0` addresses `10.10.0.1/24` and `10.10.0.2/24`, MCP2515 overlay selection, `can0` at 500000 bit/s, 120-ohm termination, CA/SAN requirements, writable paths, and service ordering.

- [ ] **Step 6: Document demonstration and failure evidence collection**

  Include normal commit, static rejection, runtime semantic rollback, heartbeat loss, power interruption recovery, and tampered archive procedures. Clearly label application A/B and the non-production Uptane-inspired scope.

- [ ] **Step 7: Commit**

  Commit message: `docs: add zonal OTA deployment and validation guide`

### Task 8: Final Verification and Push

**Files:**
- Review all changed files.

**Interfaces:**
- Consumes: the complete implementation.
- Produces: a verified commit series on `main` pushed to `origin/main`.

- [ ] **Step 1: Run formatting-independent repository checks**

  Run: `git diff --check origin/main...HEAD`
  Expected: no output.

- [ ] **Step 2: Run the complete test suite from a clean process**

  Run: `python -m pytest -q`
  Expected: all tests pass with zero failures.

- [ ] **Step 3: Inspect repository status and commit any final documentation-only corrections**

  Run: `git status --short --branch`
  Expected: clean worktree and `main` ahead of `origin/main` only by intentional commits.

- [ ] **Step 4: Review the full branch diff against the design**

  Run: `git diff --stat origin/main...HEAD` and `git log --oneline origin/main..HEAD`.

- [ ] **Step 5: Push**

  Run: `git push origin main`
  Expected: remote `main` advances to the verified final commit.
