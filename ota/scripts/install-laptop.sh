#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then echo "usage: $0 <config-path>" >&2; exit 2; fi
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then echo "run as root" >&2; exit 1; fi
config_path=$1
[[ -f $config_path ]] || { echo "configuration not found: $config_path" >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)

id capstone-ota >/dev/null 2>&1 || useradd --system --home-dir /var/lib/capstone-ota --shell /usr/sbin/nologin capstone-ota
install -d -m 0750 -o capstone-ota -g capstone-ota /var/lib/capstone-ota/releases
install -d -m 0755 /etc/capstone-ota /etc/mosquitto/conf.d
install -m 0644 "$config_path" /etc/capstone-ota/laptop.json
install -m 0644 "$repo_root/ota/broker/mosquitto.conf" /etc/mosquitto/conf.d/capstone-ota.conf
install -m 0644 "$repo_root/ota/broker/acl.template" /etc/mosquitto/acl.capstone
mosquitto -c /etc/mosquitto/mosquitto.conf -t
systemctl daemon-reload
echo "validated. Start mosquitto explicitly when ready; no service was enabled."

