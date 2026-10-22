#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then echo "usage: $0 <config-path>" >&2; exit 2; fi
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then echo "run as root" >&2; exit 1; fi
config_path=$1
[[ -f $config_path ]] || { echo "configuration not found: $config_path" >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)

id capstone-ota >/dev/null 2>&1 || useradd --system --home-dir /var/lib/capstone-ota --shell /usr/sbin/nologin capstone-ota
id digital-dash >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin digital-dash
install -d -m 0750 -o capstone-ota -g capstone-ota /var/lib/capstone-ota
install -d -m 0755 -o capstone-ota -g capstone-ota /opt/digital-dash/releases /opt/digital-dash/staging
install -d -m 0755 /opt/capstone-ota /etc/capstone-ota/pki
python3 -m venv /opt/capstone-ota/venv
/opt/capstone-ota/venv/bin/pip install --upgrade "$repo_root"
install -m 0640 -o root -g capstone-ota "$config_path" /etc/capstone-ota/agent.json
install -m 0644 "$repo_root/ota/systemd/capstone-ota-agent.service" /etc/systemd/system/capstone-ota-agent.service
install -m 0644 "$repo_root/ota/systemd/digital-dash.service" /etc/systemd/system/digital-dash.service
install -m 0644 "$repo_root/ota/polkit/50-capstone-ota.rules" /etc/polkit-1/rules.d/50-capstone-ota.rules
/opt/capstone-ota/venv/bin/python -c 'from capstone_ota.agent.config import AgentConfig; import sys; AgentConfig.from_json(sys.argv[1])' /etc/capstone-ota/agent.json
systemctl daemon-reload
echo "validated. Enable/start services explicitly after installing the initial dashboard release."
