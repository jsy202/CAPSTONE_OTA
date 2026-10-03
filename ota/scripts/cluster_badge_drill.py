#!/usr/bin/env python3
"""Hardware acceptance drill for the Cluster status badge (Cluster Pi only).

Drives the real application A/B slot API one step at a time so the on-screen
badge can be filmed with real systemd, setpriv and the watch service, before
the Central Control app and the Qt functional IPC exist. It is not an OTA
update: no signature, download or vehicle verification takes place.

Stop capstone-ota-zone-agent first (one process owns the install root) and
run as the capstone-ota account:

  sudo systemctl stop capstone-ota-zone-agent
  sudo -u capstone-ota /opt/capstone-ota/venv/bin/python ota/scripts/cluster_badge_drill.py stage \
      --payload /var/lib/capstone-ota/drill/cluster-1.1.1 --version 1.1.1
  ... activate | commit | rollback | status
  sudo systemctl start capstone-ota-zone-agent
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from capstone_ota.agent.service_manager import SystemdServiceManager
from capstone_ota.agent.slots import ABSlotInstaller
from capstone_ota.common.errors import OtaError

UNIT = "digital-cluster.service"


def main(argv: list[str] | None = None, installer_factory: Callable[[], ABSlotInstaller] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cluster status badge hardware drill")
    parser.add_argument("--install-root", type=Path, default=Path("/opt/digital-cluster"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/capstone-ota/drill"))
    parser.add_argument("step", choices=["stage", "activate", "commit", "rollback", "status"])
    parser.add_argument("--payload", type=Path, help="stage: directory containing bin/digital-dash")
    parser.add_argument("--version", help="stage: software version of the payload")
    args = parser.parse_args(argv)

    installer = (installer_factory or (lambda: ABSlotInstaller(
        args.install_root, SystemdServiceManager(allowed_units={UNIT}), service_unit=UNIT)))()
    tx_file = args.state_dir / "transaction"
    try:
        if args.step == "status":
            print(json.dumps(asdict(installer.state), indent=2, sort_keys=True))
            return 0
        if args.step == "stage":
            if not args.payload or not args.version:
                parser.error("stage needs --payload and --version")
            tx = str(uuid.uuid4())
            installer.stage(tx, args.payload, "bin/digital-dash", args.version)
            args.state_dir.mkdir(parents=True, exist_ok=True)
            tx_file.write_text(tx + "\n")
            print(f"staged {args.version} as transaction {tx}")
            return 0
        if not tx_file.exists():
            print("no staged drill transaction: stage first", file=sys.stderr)
            return 2
        tx = tx_file.read_text().strip()
        if args.step == "activate":
            installer.activate_trial(tx, rollback_on_failure=False)
        elif args.step == "commit":
            installer.commit(tx)
            tx_file.unlink()
        else:
            installer.rollback(tx)
            tx_file.unlink()
        print(f"{args.step}: phase={installer.state.phase} active={installer.state.active_slot}")
        return 0
    except OtaError as exc:
        print(f"{args.step} failed: {exc.code}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
