# Central-to-Cluster application OTA

The user authorized implementation without intermediate approval. Reuse the current
isolated feature worktree and minimal Device. Do not merge the archived zonal branch.

Central downloads and authenticates the existing three release files, then serves
only that verified temporary cache. Cluster downloads from Central, independently
checks its own pinned update key and installs into its own application A/B root.
Central never installs the Cluster release into its own slots. SSH with pinned host
keys controls Cluster; HTTP on the isolated LAN transports signed bytes, not secrets.
No broker, custom transfer protocol, OS update or distributed transaction is needed.

Cluster uses a separate fixed capstone-cluster.service. Existing digital-dash.service
is not changed automatically. A trusted external check command receives the selected
slot path AFTER initialization/service activation, must return one explicit JSON
passed=true with exit zero, and is also run after ordinary rollback. Initialization
alone is permitted only by explicit initialization_only configuration, and reported
as such. Check commands belong to deployment configuration, never to candidate metadata.

Rollback keeps the exact committed tree hash and security floor. Substituted signed
V1 must be rejected before execution. A recovery refusal does not restore service.
Qt packaging/model telemetry and a new anomaly scenario remain separate work; this
change supplies actual forwarding, target installation and an external-check hook.

Tests use real HTTP on both hops and a separate Cluster CLI process. Only the SSH
transport boundary is replaced locally; no physical three-host/CAN/Qt claim follows.
Test source-server shutdown before target download to prove Central cached the bytes.
Bound subprocess I/O and duration, reusing the existing tested process runner.
