"""The only entry point: pack a release, update a device, or read its status."""
import argparse
import json
from pathlib import Path
import sys

from .bundle import pack
from .device import Device


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
    commands.add_parser("status", help="read state without changing files or recovering")
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            result = pack(args.payload, args.output, args.version, args.svn, args.key)
        elif args.command == "update":
            result = Device(args.root).update(args.url, args.public_key, args.systemd)
        else:
            result = Device(args.root).status()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2 if result.get("result") in {"rolled_back", "failed"} else 0
    except (OSError, ValueError) as exc:
        print(f"OTA error: {exc}", file=sys.stderr)
        return 1
