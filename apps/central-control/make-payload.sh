#!/usr/bin/env bash
# Assemble the Central Control OTA payload (entrypoint bin/central-control).
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "usage: $0 <output-dir>" >&2
  exit 2
fi
out=$1
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -e $out && -n $(find "$out" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; then
  echo "refusing to write into a non-empty output directory" >&2
  exit 1
fi
install -d -m 0755 "$out/bin" "$out/lib"
install -m 0755 "$here/bin/central-control" "$out/bin/central-control"
install -m 0644 "$here/lib/central_control.py" "$out/lib/central_control.py"
echo "payload ready: $out (package with --entrypoint bin/central-control --device-id central-pi-01)"
