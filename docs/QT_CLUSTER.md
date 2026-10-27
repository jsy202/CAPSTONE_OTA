# Actual Qt Cluster, fault injection and automatic rollback

```text
Laptop signs Cluster package → Central verifies/caches → Cluster verifies/stages
                                                  ↓
                                actual Volvo Qt application + gauge models
                                                  ↑ vehicle frames
                             Central source ← independent verifier's trace
                                                  ↓
                    verifier observes CAN + Qt model/QML → approve or rollback
```

## Implemented PC scope

- Real native Qt executable using the pinned Volvo C++ models and original QML.
- Speed is read from both SpeedometerModel and the original displayed Gauge.value,
  not only from a separate test label. RPM/warnings reuse original models. A small
  overlay supplies gear and explicit stale status.
- Dynamic verification covers speed, RPM, gear and the check-engine warning. Other
  original gauges use static bench initial values; full vehicle-signal integration
  is not implied by rendering the whole upstream layout.
- Real HTTP on both OTA hops, Ed25519 signatures, target A/B and existing hash/floor.
- A separate source process delivers raw 0x200 bytes; a separate verifier process
  owns fault schedules and literal expected values. No forced FAIL API in the app.
- Standalone and three-host command paths exist. Actual three-board/ARM/systemd/CAN
  hardware is NOT tested here. PC transport is explicitly loopback UDP emulation,
  and SSH is replaced by a local command executor in the complete demo.

## The compatibility scenario

Two declared sender/receiver contracts are compared:

| Central source | Cluster receiver | Result after link recovery |
|---|---|---|
| Legacy buffered bridge | V2 source-counter filtering | Old samples ignored |
| New latest-only bridge | V3 arrival-order policy | Works with latest-only input |
| **Legacy buffered bridge** | **V3 arrival-order policy** | **Old speed briefly replaces current speed** |

The bridge's buffering/replay behavior is an application-level test model, not
automatic CAN-controller replay and not a claim of a known OEM incident. V3 changes
the receiver's dependency: freshness filtering is now expected from its peer.
Its signed configuration selects that actual policy; the verifier does not tell
the app to fail or insert impossible speed numbers inside the receiver.

Fault injection silences the input for 650 ms, then interleaves current samples
with older, CRC-valid source frames, and repeats three recovery cycles. For example,
after current 62 km/h the bridge replays old 45 km/h. V2 stays at 62, V3 shows 45.
The verifier compares a literal latest-value oracle to C++ model and actual gauge
binding after each frame. It also checks stale→valid recovery, generation/counter,
fresh response challenge, selected executable path and process identity.

Counter ordering is modulo 256 and assumes less than 128 outstanding counts.
Large gaps/restarts requiring a new source epoch are outside this small contract.
This is a bounded compatibility acceptance test. It neither proves a week of
operation nor discovers every latent failure or continuously monitors a vehicle.

## Build on PC

Python environment is the existing .venv. System requirements: C++17 compiler,
make, Qt 5.15 development tools/Core/Gui/Quick/QML/Network, QML Quick Controls,
Layouts/Shapes and GraphicalEffects. Xvfb is required for hidden screenshot capture.
There is no Python Qt binding or new pip dependency.

Use a clean source checkout pinned to the commit in
[UPSTREAM.md](../dashboard/qt_cluster/UPSTREAM.md). The current host already has
`/home/jsy/Downloads/Volvo240-DigitalDash`; build.py reads Git's pinned tree and
does not modify that checkout.

```bash
# With system Qt:
.venv/bin/python dashboard/qt_cluster/build.py --source /PATH/Volvo240-DigitalDash

# With this host's separately extracted Qt runtime/development files:
export CAPSTONE_QT_PREFIX=/home/jsy/Downloads/volvo-qt-runtime/root/usr
.venv/bin/python dashboard/qt_cluster/build.py \
  --source /home/jsy/Downloads/Volvo240-DigitalDash --qt-prefix "$CAPSTONE_QT_PREFIX"
```

Build output, imported upstream and binaries are ignored under .demo/qt-build.
Release payload contains bin/app, libexec/dashboard, cluster.json and upstream
license. QML/images are compiled into the executable. Qt libraries stay external
and must match the target architecture. PC x86 binaries must not be installed on Pi.
The same overlay can be built natively on an ARM Pi; that build is not verified here.

## Complete PC demo

```bash
.venv/bin/python -m demo.qt_run run --output .demo/qt-complete-run
.venv/bin/python -m pytest -q
```

Use a NEW output directory each time. Keep CAPSTONE_QT_PREFIX exported if system
Qt is not installed. Tests load the recorded local build runtime for their child
processes. Qt tests are explicitly skipped when no compiled Qt binary is available;
a Python-only green suite is not proof of Qt integration. They run fully here.

Expected complete-demo results:

1. V2 baseline accepted; compatible V3/latest-only pairing committed.
2. V3/buffered pairing rejected after real display regression; V2 restored and
   the same fault profile passes again.
3. Test fixture substitutes a genuine signed V1 only after observing V3 failure.
   Journal remains unchanged. Recovery hash mismatch blocks V1 before execution.

result.json includes actual model observations, per-trial PIDs and Qt captures.
Signing keys stay in a private sibling keys/ directory, never in HTTP roots/Git.
V1 is a controlled old-release fixture, not a real exploitable vulnerability.
Normal tests use offscreen mode. Captures use a private Xvfb display, so repeated
tests do not open windows on the user's desktop. bin/app --check is always headless.
Native manual execution without --check may show the actual application window.
The lower-right version badge reads the signed cluster.json version field: V1 is
purple, V2 blue and V3 orange. This visual label is separate from the updater's
signed release/security-version checks. V1 style previews do not imply execution
of the blocked recovery image in the attack test.

## Three-host execution paths

Use the transport setup in [CLUSTER_OTA.md](CLUSTER_OTA.md). Install the new package
on each host. The runtime service must start the selected Cluster bin/app with a
CAN interface and model socket; deploy/capstone-cluster.service is an example for
/opt/capstone-cluster/device. Configure its display/session/Qt libraries explicitly.
It does not replace existing digital-dash.service automatically.

Trusted SSH directions: Central→Cluster, Cluster→Verifier, Verifier→Central and
Verifier→Cluster. Set dedicated identities and pinned host keys beforehand.
The Cluster observation account needs access to the private runtime socket/group.

On Pi3, place a copy of deploy/qt-verifier-peers.example.json with actual host paths,
the Cluster runtime socket and Pi3's own observe_channel. Pi3 opens its own raw CAN
socket and confirms each requested frame directly, not from Central's success claim.
On Cluster, place qt-verifier-gate.example.json pointing at that Pi3 config.
Replace initialization_only in Central's target JSON with:

```json
"check_command": [
  "/home/otauser/CAPSTONE_OTA_MINIMAL/.venv/bin/python",
  "-m", "capstone_ota.qt_verify", "remote-gate",
  "--config", "/home/otauser/qt-verifier-gate.json"
]
```

Set systemd=true only after the separate Cluster service/path has been provisioned.
Cluster starts the candidate, then invokes Pi3. Pi3 controls Central's qt_source
and reads actual selected Qt model via the Cluster-local snapshot command. It
checks executable path/PID/challenge and reports to Cluster over authenticated SSH.
Each external acceptance check is bounded to 60 seconds; remote-gate to 50 seconds.
Any missing evidence, mismatch, process exit or timeout rejects approval.

All tests must run on an isolated bench with no other source sending 0x200; a
single-byte/source-counter protocol is not cryptographic sender authentication.
The remote gate assumes a freshly activated/restarted service for each acceptance
trial. It is not a repeatable live test on an arbitrary existing counter epoch.
The emulated_udp option exists only for same-machine command-path tests, not LAN
UDP deployment. Physical CAN timing, bitrate, bus-off, HAT wiring, actual SSH host
authentication, Pi display and power-cut behavior remain hardware validation.

## Limits of the evidence

Qt model/gauge telemetry is still reported by Cluster software. It proves the
tested code's model/binding behavior, not independent pixels or protection against
a malicious signed UI lying about its output. Captures aid human review and are not
an automated optical oracle. Updating a slot on PC is not OS A/B or secure boot.
After refusing substituted recovery, service restoration remains intentionally absent.

## Validation record — 2026-10-27

- Final full suite: **100 passed in 84.04s**, with all native Qt tests executed.
- Rebuilt real C++/QML application; package wheel built and tested from outside the
  source checkout against the installed V2 Qt payload and fault profile.
- Complete signed two-hop demo: compatible V3 committed, mixed V3 rejected with
  observed gauge regression and V2 recheck/rollback, genuine V1 substitution refused
  without executing its marker. Replayed frames retain their original counter/data.
- Independent review corrections: exact replay data consistency and unrelated CAN-ID
  isolation; regressions failed before fixes and pass afterward.
- Evidence: .demo/qt-validation-2026-10-27-final/result.json and private virtual-screen
  captures. No original Downloads source, existing dashboard service or Pi OS changed.
