#!/usr/bin/env bash
# Apply the CAPSTONE OTA status badge customization to a pinned upstream import.
#
#   ./import-upstream.sh            # fetch pinned upstream (unchanged script)
#   ./customization/apply-customization.sh upstream/QtDash/VolvoDigitalDashModels
#
# Copies overlay/ (new files only) and applies one add-only patch to four
# upstream files. Refuses to run twice or on a tree that is not an import.
set -euo pipefail

pinned=793452919127065536bcb7a08f98838fa963d75e
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
patch_file=$here/patches/0001-capstone-ota-status-badge.patch

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <imported VolvoDigitalDashModels directory>" >&2
  exit 2
fi
target=$1
marker=$target/.capstone-customization-applied

for required in app/main.qml app/app.pro app/qml.qrc app/src/main.cpp; do
  if [[ ! -f $target/$required || -L $target/$required ]]; then
    echo "not a VolvoDigitalDashModels import (missing $required): $target" >&2
    exit 1
  fi
done
if [[ -e $marker ]]; then
  echo "customization already applied: $target" >&2
  exit 1
fi
for new_file in $(cd "$here/overlay" && find . -type f); do
  if [[ -e $target/$new_file ]]; then
    echo "overlay file already exists in target: $new_file" >&2
    exit 1
  fi
done

patch -p1 --forward --dry-run --silent -d "$target" < "$patch_file"
cp -R -- "$here/overlay/." "$target/"
patch -p1 --forward --silent -d "$target" < "$patch_file"
printf 'upstream=%s\npatch=%s\n' "$pinned" "$(basename -- "$patch_file")" > "$marker"
echo "applied CAPSTONE OTA status badge customization to $target"
