# Separate Qt OTA progress panel

User requested a Qt panel beside the dashboard, surviving dashboard replacement.
Implement directly under the previously agreed autonomous workflow.

Use a separate native Qt Quick process, polling a read-only progress.json outside
application slots. Device writes best-effort telemetry: stage, timestamp, PID,
versions, measured payload bytes/Content-Length, and last verification mismatch.
Only downloads get a percentage; absent/unreliable length stays indeterminate.
Stages represent actual completed/running operations, never timed fake progress.
Missing/invalid/stale telemetry must not imply success. A stopped nonterminal
writer is shown as unconfirmed. Display committed version separately from running
candidate: retained V2 metadata after recovery refusal does not imply healthy V2.

Keep verifier verdicts, rollback hashes, security state and failure policy intact.
Telemetry failures never abort updates. No network service, Python Qt dependency,
hardware button work, new compatibility scenario or OS OTA in this change.

Alternatives: embedding in candidate hides progress on restart; a browser panel
misses the requested Qt presentation. Choose independent Qt Quick window.
Provide a PC live presentation option and captured actual Qt evidence. Physical
multi-board display wiring and remote telemetry transport remain unverified.
