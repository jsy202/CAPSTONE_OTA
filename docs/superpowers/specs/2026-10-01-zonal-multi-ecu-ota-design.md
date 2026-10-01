# Zonal Multi-ECU OTA Compatibility and Rollback Design

## 1. Purpose

Extend CAPSTONE_OTA from a single Raspberry Pi application updater into a
two-ECU zonal-architecture demonstrator. The system updates a Central Control
application and a Qt Digital Cluster application as one vehicle release,
checks their declared dependencies and observed CAN behavior, and accepts the
release only when every check succeeds. If either trial application fails, the
system restores both ECUs to their previous stable application slots and
verifies recovery.

This is an application OTA demonstrator. Raspberry Pi OS, the Linux kernel,
the bootloader, the update coordinator, and the per-device update agents are
not update targets.

## 2. Hardware and Network Topology

The demonstrator uses one laptop and two Raspberry Pis.

```text
Laptop OTA backend
        |
        | Wi-Fi: MQTT control and HTTPS artifact download
        v
Central HPC Raspberry Pi
  - wlan0: laptop-facing network
  - eth0: 10.10.0.1/24, private update network
  - can0: 500 kbit/s Classic CAN
        |\
        | \ CAN: commands, status, heartbeat, vehicle data, validation
        |  \
        |   v
        +-- Ethernet -- Cluster Raspberry Pi
                       - eth0: 10.10.0.2/24
                       - can0: 500 kbit/s Classic CAN
```

Both Raspberry Pis use MCP2515-based CAN HATs. The two-node CAN bus uses a
120-ohm termination at each physical end and a shared ground. The Ethernet
link carries release metadata and application archives; CAN carries only
compact coordination, status, and runtime data frames.

The Cluster ECU does not contact the laptop. The Central HPC is the only
vehicle-side node exposed to the external OTA backend and acts as the update
gateway for the Cluster ECU.

## 3. Logical Components

### 3.1 Laptop OTA backend

The existing publisher remains responsible for building deterministic
application archives, creating and signing release manifests, serving data
over HTTPS, and initiating an update over MQTT. It gains support for a signed
vehicle bundle manifest containing the two ECU releases and their compatibility
requirements.

### 3.2 Vehicle Update Coordinator

The coordinator runs as a stable system service on the Central HPC. It:

- receives a vehicle bundle command from the laptop;
- verifies the signed bundle and both signed ECU release manifests;
- downloads and caches both release archives;
- validates declared dependencies before activation;
- exposes the Cluster release through a private HTTPS endpoint on `eth0`;
- coordinates prepare, activation, verification, commit, and rollback;
- persists the vehicle transaction state before externally visible actions;
- reports aggregate progress to the laptop; and
- recovers an interrupted transaction after restart.

There is exactly one active coordinator. This avoids competing authorities
activating inconsistent ECU combinations.

### 3.3 Central Control ECU application

The Central Control application is an update target separate from the
coordinator. It publishes simulated vehicle signals such as speed, RPM, gear,
and warning flags on CAN. It also participates in functional validation.

### 3.4 Cluster Update Agent and Qt application

The Cluster Update Agent receives compact commands over CAN, retrieves detailed
metadata and its archive from the Central HPC over HTTPS, manages its local
application slots, and returns status over CAN. The Qt Digital Cluster
application receives and displays the Central Control application's vehicle
signals and returns functional-test observations.

## 4. Trust and Transport Boundaries

- MQTT over mutual TLS is used only between the laptop and Central HPC.
- HTTPS with certificate verification transports metadata and archives.
- ECU and bundle manifests are signed with Ed25519.
- Archive size and SHA-256 are checked before extraction.
- Existing safe archive extraction rules remain mandatory.
- CAN CRC and rolling counters detect corruption, loss, and stale frames but do
  not provide cryptographic sender authentication.
- CAN intrusion detection and production key lifecycle management are outside
  this capstone's scope.

The Central HPC may cache verified bytes, but it cannot authorize modified
content because each ECU release remains independently signed.

## 5. Metadata Model

### 5.1 ECU release manifest

The current `ReleaseManifest` remains unchanged as the signed description of
one application archive. Each vehicle bundle target wraps a release-manifest
reference with:

- `ecu_id`;
- `hardware_id`;
- semantic `software_version`;
- application entry point;
- declared CAN interface name and protocol major/minor;
- provided capabilities; and
- archive URL, size, and SHA-256.

### 5.2 Vehicle bundle manifest

The signed vehicle bundle is the unit of compatibility and rollback. It
contains:

- a UUID `transaction_id`;
- a semantic `bundle_version`;
- exactly one target for `central-control` and one for `digital-cluster` in
  the initial demonstrator;
- references to both signed ECU manifests;
- dependency rules between the ECU targets;
- verification timeouts and thresholds;
- `rollback_scope: all`; and
- creation and expiration times.

An ECU release may be reused in different compatible bundles, but a bundle is
accepted only when all referenced releases and dependency rules validate.

### 5.3 Dependency rules

Initial rules support:

- required ECU presence;
- hardware ID matching;
- minimum/maximum software versions;
- exact CAN protocol major compatibility;
- minimum CAN protocol minor version; and
- required capability names.

Protocol major mismatch is a pre-activation error. Minor versions may differ
only when the provider and consumer declarations prove backward compatibility.

## 6. Application A/B Slots

Each update target has two application slots under its own install root.

```text
/opt/<application>/
  slots/A/
  slots/B/
  active-slot
  state.json
```

Slot state distinguishes `stable`, `inactive`, and `trial`. An archive is
written only to the inactive slot. Activation atomically changes the selected
slot and restarts the application service. A trial slot becomes stable only
after an explicit commit. Timeout, reboot before commit, or an explicit
rollback restores the prior stable slot.

This is application-level A/B. It is not a Raspberry Pi OS or root filesystem
A/B implementation.

## 7. Vehicle Transaction State Machine

The coordinator persists these states:

```text
IDLE
  -> PREPARING
  -> READY
  -> ACTIVATING
  -> VERIFYING
  -> COMMITTED

PREPARING  -> ABORTED
READY      -> ABORTED
ACTIVATING -> ROLLING_BACK
VERIFYING  -> ROLLING_BACK
ROLLING_BACK -> RECOVERY_VERIFYING
RECOVERY_VERIFYING -> ROLLED_BACK
RECOVERY_VERIFYING -> RECOVERY_FAILED
```

### 7.1 Prepare

The coordinator verifies and caches the entire bundle before instructing any
application to change slots. The Central and Cluster agents then install their
archives in inactive slots and report readiness. If either agent fails, the
transaction is aborted without disturbing the running applications.

### 7.2 Activate trial

After both agents are ready, the coordinator places vehicle data output in a
maintenance state, activates both trial slots, and waits for fresh heartbeats.
The coordinator service itself remains running throughout activation.

### 7.3 Verify

Verification has three layers:

1. Process health: both services stay active and report the expected software
   and protocol versions.
2. CAN contract health: expected IDs, DLCs, periods, counters, and value ranges
   are observed within configured limits.
3. Functional health: the coordinator sends known speed, RPM, gear, and
   warning inputs; the Cluster reports the values it interpreted and displayed.

### 7.4 Commit

Only after every verification passes does the coordinator commit both trial
slots. The bundle version and ECU version set become the new stable vehicle
state.

### 7.5 Whole-bundle rollback

Any activation or verification failure rolls back both applications, even if
one appears healthy. The Cluster is instructed to restore its prior slot, the
Central Control application restores its prior slot, and the coordinator then
repeats CAN and functional checks against the prior stable version set. A
failed recovery enters `RECOVERY_FAILED` and requires manual intervention; it
must never be reported as a successful rollback.

## 8. CAN Contract

The initial design uses 11-bit identifiers and eight-byte Classic CAN frames.

| CAN ID | Direction | Purpose |
|---|---|---|
| `0x100` | Central to Cluster | Central heartbeat and version |
| `0x101` | Cluster to Central | Cluster heartbeat and version |
| `0x200` | Central to Cluster | Vehicle status signals |
| `0x600` | Central to Cluster | OTA transaction command |
| `0x601` | Cluster to Central | OTA transaction status |
| `0x610` | Central to Cluster | Functional test request |
| `0x611` | Cluster to Central | Functional test result |

The OTA command contains a command enum, a 32-bit transaction token mapped by
the coordinator to the bundle's UUID, target slot, flags, and CRC-8. A token
collision with an active or retained transaction is rejected. Commands include
`PREPARE`, `ACTIVATE`, `COMMIT`, `ROLLBACK`, and `QUERY_STATUS`. Detailed
manifests are fetched from the Central HPC's fixed HTTPS transaction endpoint
instead of being embedded in CAN frames.

Heartbeats report compact numeric software and protocol versions, trial/stable
state, a rolling counter, and CRC-8. A missing or stale heartbeat fails runtime
verification.

## 9. Interruption and Recovery Rules

Coordinator and agent state files are written atomically and include the
transaction ID, stable slot, trial slot, current phase, attempts, and last
error. On restart:

- `PREPARING` removes incomplete staging data and keeps stable slots active;
- `READY` aborts unless the same signed bundle is explicitly resumed;
- `ACTIVATING` or `VERIFYING` initiates whole-bundle rollback;
- `ROLLING_BACK` resumes rollback idempotently;
- `COMMITTED` keeps the committed slots active; and
- unknown or corrupt state blocks activation and requires recovery rather than
  guessing a safe version.

Repeated commands for a completed transaction return the recorded result and
do not reinstall software.

## 10. Demonstration Scenarios

The acceptance demonstration includes:

1. Successful update of both applications followed by commit.
2. Declared CAN major mismatch rejected during prepare without activation.
3. A Cluster trial version that declares the correct protocol but interprets a
   signal incorrectly; functional verification detects the defect and both
   applications roll back.
4. Cluster heartbeat loss after activation; timeout causes whole-bundle
   rollback.
5. Power interruption during verification; restart resumes safe rollback and
   verifies the previous version set.
6. Modified archive; signature or hash verification rejects it before staging.

The primary automatic-rollback demonstration uses a runtime semantic defect,
not a declared protocol mismatch, because a robust static compatibility check
must reject known mismatches before activation.

## 11. Observability

Every transition records timestamp, transaction ID, bundle version, ECU states,
active/trial slots, failure code, and verification evidence. The laptop status
view receives aggregate stages and per-ECU summaries. Logs must distinguish:

- update aborted before activation;
- trial activation failed;
- compatibility verification failed;
- rollback completed and recovery verified; and
- rollback or recovery verification failed.

## 12. Testing Strategy

Unit tests cover bundle parsing, duplicate-field rejection, signature checks,
dependency resolution, CAN frame encoding/decoding, state transitions,
idempotency, and slot selection. Integration tests use fake service and CAN
adapters to exercise success, prepare failure, trial failure, rollback, and
restart recovery. Hardware validation on the two Raspberry Pis records CAN
traffic, service logs, slot state, displayed values, and recovery results for
each demonstration scenario.

## 13. Non-Goals and Limitations

- Raspberry Pi OS, kernel, bootloader, and root filesystem updates
- full Uptane or TUF repository implementation
- production automotive safety certification
- cryptographic CAN message authentication
- multi-primary failover
- partial or dependency-subgraph rollback
- firmware transport over Classic CAN
- more than two physical ECUs in the initial demonstrator

The software architecture should avoid hard-coding two ECUs where practical,
but acceptance and hardware testing target the Central Control and Digital
Cluster pair only.

## 14. Rationale

Separating the stable coordinator from update targets preserves a recovery
authority during application failure. Application A/B slots demonstrate safe
trial and rollback behavior without expanding the project into bootloader and
root filesystem engineering. A signed bundle makes compatibility and rollback
a vehicle-level decision, while independently signed ECU releases preserve
artifact integrity through the Central HPC cache.

Ethernet keeps large archives off the real-time CAN bus. CAN remains essential:
it carries the actual ECU contract and supplies observed evidence that declared
versions work together. Static dependency checks prevent known bad
combinations; runtime functional checks detect implementation bugs that version
metadata cannot prove absent. Whole-bundle rollback avoids leaving the vehicle
in an untested mixed-version state.

The coordinator/secondary split follows the high-level Uptane Primary and
Secondary model, but the transactional activation and whole-bundle rollback
policy are project-specific. Uptane secures metadata and image selection but
does not guarantee atomic installation of a bundle across arbitrary ECU
failures.
