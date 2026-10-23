# Three-Pi compatibility bench

Central owns signed application directory A/B OTA. Cluster runs a fixed receiver.
Verifier owns the expected speed inputs, observes CAN directly, and compares the
Cluster's reported accepted model values. SSH controls execution; CAN carries
vehicle samples, test-start challenges and Cluster diagnostic replies.

Use standard-library Linux SocketCAN and existing SSH, signatures and Device.
No new broker, generic rule engine, OS partitions or automatic service recovery.
Reuse the existing dedicated worktree. User has requested autonomous execution.

Compared approaches: LAN-only nodes are easy to run but would leave CAN missing;
SocketCAN plus SSH supplies the actual three-host execution path without adding
network services. The latter is the implementer's choice. Physical testing remains
pending hardware. The third Pi must already have SSH access to Central.

Scenario: a signed candidate changes its CRC protection Data ID while the fixed
Cluster retains the old setting. Raw identifier and payload size still match;
initialization passes, but the Cluster rejects samples. This is a simplified
Data-ID-bound CRC demonstrator, not an implementation of an AUTOSAR E2E profile
or evidence of a particular OEM incident. CRC does not authenticate a sender.

An eight-byte start frame carries a random four-byte challenge and magic tag.
Cluster reset is a bench-only diagnostic command. It acknowledges the challenge,
then reports each consumed sample using that challenge. Verifier rejects missing,
extra, malformed, stale, or mismatching samples and never accepts a Central-only
success report. Cluster reports are self-reports, not independent pixel evidence.
CAN is unauthenticated; challenge matching is correlation, not anti-forgery.

Remote OTA gate runs this verifier through pinned-host SSH before committing and
again during normal rollback. It remains a separate bench CLI; the basic updater
is unchanged. The existing signed-V1 slot-substitution rejection stays unchanged;
safe service recovery after a refusal and real V1 exploitation remain out of scope.

Trust boundary: protected OTA journal/updater/verifier, trusted SSH identities and
fixed Cluster receiver. This is a stationary isolated test bench, not road testing.
