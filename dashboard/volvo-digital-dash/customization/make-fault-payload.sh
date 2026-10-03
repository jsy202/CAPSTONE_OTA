#!/usr/bin/env bash
# DEMO/TEST FAULT INJECTION ONLY.
# Wrap a Cluster binary built with `qmake CAPSTONE_FAULT_SPEED_DIVISOR=10` into a
# clearly labelled OTA payload. Its speed interpretation (display and functional
# observation alike) is deliberately wrong, so trial verification must report
# FUNCTIONAL_VALUE_MISMATCH and roll the whole vehicle back. Never ship it.
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "usage: $0 <fault-built-binary> <output-dir>" >&2
  exit 2
fi
if ! grep -aq "DEMO/TEST FAULT INJECTION BUILD" -- "$1"; then
  echo "binary is not a fault-injection build (marker missing)" >&2
  exit 1
fi
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CAPSTONE_ALLOW_FAULT_PAYLOAD=1 "$here/../make-payload.sh" "$1" "$2" >/dev/null
printf 'DEMO/TEST FAULT INJECTION ONLY\nspeed interpretation divided on purpose; expected verdict FUNCTIONAL_VALUE_MISMATCH\n' \
  > "$2/FAULT-INJECTION-ONLY"
echo "fault-injection payload ready: $2 (DEMO/TEST ONLY)"
