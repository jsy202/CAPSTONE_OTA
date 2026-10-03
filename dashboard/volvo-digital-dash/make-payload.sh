#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <built-binary> <output-dir> [resources-dir]" >&2
  exit 2
fi
built_binary=$1
output_dir=$2
resources_dir=${3:-}

if [[ ! -f $built_binary || -L $built_binary || ! -x $built_binary ]]; then
  echo "built binary must be an executable regular file" >&2
  exit 1
fi
if grep -aq "DEMO/TEST FAULT INJECTION BUILD" -- "$built_binary" && [[ ${CAPSTONE_ALLOW_FAULT_PAYLOAD:-} != 1 ]]; then
  echo "refusing a DEMO/TEST FAULT INJECTION build; use customization/make-fault-payload.sh" >&2
  exit 1
fi
if [[ -e $output_dir && -n $(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; then
  echo "refusing to write into a non-empty output directory" >&2
  exit 1
fi
if [[ -n $resources_dir ]]; then
  [[ -d $resources_dir && ! -L $resources_dir ]] || { echo "invalid resources directory" >&2; exit 1; }
  if find "$resources_dir" -type l -o \! -type f -a \! -type d | grep -q .; then
    echo "resources may contain only regular files and directories" >&2
    exit 1
  fi
fi

install -d -m 0755 "$output_dir/bin"
install -m 0755 "$built_binary" "$output_dir/bin/digital-dash"
if [[ -n $resources_dir ]]; then
  install -d -m 0755 "$output_dir/resources"
  cp -a -- "$resources_dir/." "$output_dir/resources/"
fi

echo "payload ready: $output_dir"
echo "package with: capstone-ota-publish package --payload '$output_dir' --entrypoint bin/digital-dash --output RELEASE_DIR --device-id DEVICE_ID --version VERSION --base-url https://LAPTOP_IP:8443 --private-key /etc/capstone-ota/pki/update-signing.key"
