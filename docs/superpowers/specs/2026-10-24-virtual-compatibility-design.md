# Virtual compatibility and rollback substitution demonstration

## Intent

Show one coherent chain before touching hardware: a signed, successfully initialized Central update breaks a fixed Cluster's sequence contract on reconnect; an independent process detects it; the real OTA engine restores V2 or refuses a recovery slot replaced with an older signed V1.

The user selected virtual-first and has already requested autonomous execution without intermediate approvals. The following bounded implementation choices are the builder's defaults, not claims of already completed hardware.

## Approaches

1. Physical CAN/vcan plus Pi deployment: adds kernel/network setup before the defect is understood. Defer.
2. Separate processes exchanging JSON lines containing real eight-byte `0x200` payloads: deterministic, no administrator privileges, directly exercises publisher and receiver state machines. Selected.
3. In-process mocked pass/fail or simple changed image: cannot establish the processing defect. Reject.

## Parts

- Keep all production `capstone_ota/` code and the basic CLI unchanged. A demo-only `VerifiedDevice` subclasses Device to add a bounded external verifier to its initialization check. This is an integration experiment, not a deployed third-Pi enforcement service.
- Central payload has normal `bin/app --check` plus a line-based sample/reconnect interface. The same publisher code handles runtime samples. V2 preserves its Sender across reconnect; V3 reconnect reconstructs Sender and resets its rolling counter. No forced verdict/failure flag.
- Fixed virtual Cluster accepts a first frame and then contiguous modulo-256 counters. It does not reset its receive state when Central's input source reconnects. That is the explicit demo contract, not a claim all production ECUs behave this way.
- Start at counter 250 and send eight samples, crossing 255->0 normally. Reconnect, then send six new values. V2 sends counters 2..7; V3 sends 250..255 and the receiver rejects them. Initialization, CRC, encoding and pre-reconnect processing all remain normal.
- The verifier launches Central and Cluster as separate subprocesses, observes raw frames and Cluster's accepted model values, and compares them to its own fixed input sequence. Whole subprocess exchanges have deadlines and output bounds. It returns a JSON evidence record, not just the application's own claim.
- The runner makes real signed V1 (1.0.0/SVN1), V2 (2.0.0/SVN2), V3 (3.0.0/SVN2) packages, serves them over loopback HTTP and uses the real filesystem A/B updater. V1 is an obsolete-policy fixture, not an implemented real-world exploit.
- Run two fresh device roots: normal V3 rejection and V2 recovery; then controlled slot substitution immediately after V3 compatibility failure and before recovery. Verify V1's signature, overwrite only the demo's recorded V2 slot from V1's payload, leave state/public key/verifier untouched. The expected tree-hash binding must reject recovery before invoking V1.

## Evidence and boundaries

Single command: `python -m demo.run --output .demo/compatibility`. Refuse an existing output directory. Store keys privately outside served releases and put reports under the chosen demo directory. Final result separates compatibility failure, normal rollback and signed-old-image rejection.

No actual CAN interface, arbitration/timing, Qt pixels, physical three-Pi independence, root-compromise protection, vulnerability discovery or restored safe driving is claimed. Transport is stdin/stdout simulation. The selected receiver rule is deliberately strict; other receivers may resynchronize differently. The attack injector has fixture-only permission to change the slot, not the trusted journal; this does not demonstrate how an attacker obtains that permission on a real Pi.

No MQTT, generic contract engine, extra signing service, new default OTA command, production fault-injection option or Pi service change. Branch `feature/virtual-compatibility-demo` preserves the minimal baseline.
