#!/usr/bin/env bash
set -euo pipefail

repository=https://github.com/whitfijs-jw/Volvo240-DigitalDash.git
commit=793452919127065536bcb7a08f98838fa963d75e
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target=${1:-$script_dir/upstream/QtDash/VolvoDigitalDashModels}

if [[ -e $target ]]; then
  echo "refusing to overwrite existing import: $target" >&2
  exit 1
fi

temporary=$(mktemp -d)
cleanup() { rm -rf -- "$temporary"; }
trap cleanup EXIT
git -C "$temporary" init -q
git -C "$temporary" remote add origin "$repository"
git -C "$temporary" fetch -q --depth 1 origin "$commit"
git -C "$temporary" archive FETCH_HEAD QtDash/VolvoDigitalDashModels | \
  tar -x -C "$temporary" \
    --exclude='.qmake.stash' \
    --exclude='VolvoDigitalDashModels/app/VolvoDigitalDashModels' \
    --exclude='*.autosave.*' --exclude='*.D[0-9]*' --exclude='*.z[0-9]*'
mkdir -p -- "$(dirname -- "$target")"
cp -a -- "$temporary/QtDash/VolvoDigitalDashModels" "$target"
echo "imported $repository@$commit to $target"

