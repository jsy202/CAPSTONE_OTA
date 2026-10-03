#!/usr/bin/env bash
# One-command verification of the Cluster OTA status badge and the OTA flows
# it depends on. Optional environment:
#   CAPSTONE_QT_DIR        Qt 5.15 prefix -> Qt unit/QML component tests run
#   CAPSTONE_UPSTREAM_DIR  pinned upstream import -> real patch application runs
# Without them those measures are reported NOT EXECUTED (never PASS).
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
exec python3 scripts/verify_cluster_ota.py "$@"
