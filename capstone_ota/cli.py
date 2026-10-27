"""Package, forward to Cluster, install/recover locally, or inspect a device."""
import argparse
import json
from pathlib import Path
import sys

from .bundle import pack
from .device import Device
from .cluster import ClusterDevice, check_command
from .relay import forward


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Minimal signed application OTA (directory A/B, not OS partitions)")
    parser.add_argument("--root", type=Path, default=Path("device"), help="device directory (default: ./device)")
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("pack", help="create three release files on the laptop")
    package.add_argument("--payload", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--svn", type=int, required=True)
    package.add_argument("--key", type=Path, required=True, help="Ed25519 PRIVATE key; keep on the laptop")
    update = commands.add_parser("update", help="install a URL; without URL, recover an interrupted trial")
    update.add_argument("url", nargs="?")
    update.add_argument("--public-key", type=Path, help="trusted PUBLIC key, required on first install")
    update.add_argument("--systemd", action="store_true", help="first-install opt-in to capstone-central.service")
    target = commands.add_parser("cluster-update", help="install/recover on this Cluster, not on Central")
    target.add_argument("url", nargs="?")
    target.add_argument("--public-key", type=Path)
    validation = target.add_mutually_exclusive_group(required=True)
    validation.add_argument("--check-command", help="trusted external command as a JSON argument array; slot path appended")
    validation.add_argument("--initialization-only", action="store_true")
    target.add_argument("--systemd", action="store_true", help="first-install opt-in to capstone-cluster.service")
    relay = commands.add_parser("forward", help="Central verifies/caches a release and sends it to Cluster")
    relay.add_argument("url")
    relay.add_argument("--public-key", required=True, type=Path, help="Central's trusted PUBLIC update key")
    relay.add_argument("--target", required=True, type=Path, help="Cluster target JSON; contains no private keys")
    relay.add_argument("--bind", default="127.0.0.1")
    relay.add_argument("--advertise", help="Central IPv4/DNS host reachable by Cluster")
    commands.add_parser("status", help="read state without changing files or recovering")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            result = pack(args.payload, args.output, args.version, args.svn, args.key)
        elif args.command == "update":
            result = Device(args.root).update(args.url, args.public_key, args.systemd)
        elif args.command == "cluster-update":
            checker = check_command(json.loads(args.check_command)) if args.check_command is not None else None
            device = ClusterDevice(args.root, checker)
            try:
                result = device.update(args.url, args.public_key, args.systemd)
            except (OSError, ValueError) as exc:
                result = device.status()
                if result["result"] != "recovery_failed":
                    raise
                result.update(passed=False, error=str(exc))
            result.update(validation="external" if checker else "initialization_only", verification=device.reports)
        elif args.command == "forward":
            result = forward(args.url, args.public_key, json.loads(args.target.read_text()), args.bind, args.advertise)
        else:
            result = Device(args.root).status()
        print(json.dumps(result, indent=None if args.command == "cluster-update" else 2, sort_keys=True))
        return (1 if result.get("result") == "recovery_failed" else
                2 if result.get("result") in {"rolled_back", "failed"} else 0)
    except (OSError, ValueError) as exc:
        print(f"OTA error: {exc}", file=sys.stderr)
        return 1
