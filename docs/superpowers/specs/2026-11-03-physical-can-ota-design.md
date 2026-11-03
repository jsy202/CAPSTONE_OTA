# Physical CAN + Qt OTA completion

User requests autonomous project completion, actual CAN/Qt/OTA/rollback tests and
career-oriented verification tools and records. No further permission gates for
authorized routine setup, fixes, tests, commits or pushing main.

## Scope
Two Pi4B boards are physically connected via MCP2515/12MHz CAN and direct Ethernet.
Pi1 Central: Wi-Fi 192.168.45.171, Ethernet10.77.0.1, existing dashboard preserved.
Pi2 Cluster: Ethernet10.77.0.2. Both can0 up at500kbit/s; 3 exact frames each way
passed with can-utils, zero errors. Third Pi Wi-Fi/SSH works but CAN is not wired
and sudo currently needs a password. Do not claim independent third-board CAN.

Use a separate capstone-cluster.service on Pi2 running selected native ARM Qt
continuously, as a restricted app user. Updater/journal/keys stay outside writable
app scope. Observe its actual model/QML over a Unix socket, verify native CAN
frames on the Cluster interface, and command the real Central source over SSH.
Support this two-board observer through a local socket option in the existing
remote verifier config; preserve the three-board observer mode and fail-closed
verdicts. A bounded ready wait handles real service startup without fake results.

Signed release V2 filters old frames; V3 arrival-order policy. Test normal V3 on
latest-only source, replay V3 rejection + V2 recheck, and genuine signed V1 recovery
substitution rejection before execution. This remains a constructed sender contract
regression, not a proven OEM incident. No automatic restoration after V1 refusal.

## Evidence and tools
pytest hardware tests opt in with external host config, fail (not skip) if selected
hardware is unavailable, run sequentially, use fresh isolated device roots. Record
requirements/TC IDs, commit/binary versions, inputs, expected/actual, exact CAN
logs, Qt captures, state and failure causes. can-utils provides bus capture/load;
tcpdump/tshark provide real Ethernet capture/analysis. pytest-cov covers Python,
Allure attaches evidence; distinguish unrun/skipped from passed. No private keys
or Wi-Fi secrets in artifacts/Git. No percentages or transport independence claims
without actual measurement.

Native CAN cases: exact two-way frames; valid speed/RPM/gear/warning; CRC failure
ignored; source counter wrap; stale replay ignored by V2; silence invalidates input;
fresh frame recovers; bounded unrelated-ID traffic leaves vehicle evidence intact.
OTA cases: signed transport/normal commit; regression rejection/recovery; signed
old recovery refused; missing verifier evidence prevents commit. Power-cut,
physical HDMI/pixel verification and sustained endurance remain explicitly untested.

Finish independent existing Qt progress work in its original worktree, merge only
after fresh tests. Keep project dependencies in development extras. Reuse core
functions, no new protocol framework/MQTT/ML infrastructure. Final main push and
downloadable runbook, evidence and honest remaining limitations are authorized.
