from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import generate_key_pair

from .bundle import BundleRequest, ReleaseBundle, build_release
from .http_server import create_https_server
from .mqtt import PublisherMqtt


def _https_base(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("URL must use HTTPS without embedded credentials")
    return value.rstrip("/")


def build_command(bundle: ReleaseBundle, base_url: str) -> dict[str, object]:
    base_url = _https_base(base_url)
    return {
        "schema_version": 1,
        "job_id": bundle.manifest.job_id,
        "device_id": bundle.manifest.device_id,
        "version": bundle.manifest.version,
        "manifest_url": f"{base_url}/{bundle.manifest_path.name}",
        "signature_url": f"{base_url}/{bundle.signature_path.name}",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="capstone-ota-publish")
    commands = parser.add_subparsers(dest="command", required=True)

    keys = commands.add_parser("keys", help="generate an Ed25519 update key pair")
    keys.add_argument("--private-key", required=True, type=Path)
    keys.add_argument("--public-key", required=True, type=Path)

    package = commands.add_parser("package", help="build and sign a release")
    package.add_argument("--payload", required=True, type=Path)
    package.add_argument("--output", required=True, type=Path)
    package.add_argument("--device-id", required=True)
    package.add_argument("--version", required=True)
    package.add_argument("--entrypoint", required=True)
    package.add_argument("--base-url", required=True, type=_https_base)
    package.add_argument("--private-key", required=True, type=Path)
    package.add_argument("--expires-in", type=int, default=3600)

    serve = commands.add_parser("serve", help="serve release files over TLS")
    serve.add_argument("--root", required=True, type=Path)
    serve.add_argument("--bind", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8443)
    serve.add_argument("--cert", required=True, type=Path)
    serve.add_argument("--key", required=True, type=Path)

    for name in ("publish", "watch"):
        mqtt_parser = commands.add_parser(name)
        mqtt_parser.add_argument("--broker", required=True)
        mqtt_parser.add_argument("--port", type=int, default=8883)
        mqtt_parser.add_argument("--ca", required=True, type=Path)
        mqtt_parser.add_argument("--cert", required=True, type=Path)
        mqtt_parser.add_argument("--key", required=True, type=Path)
        mqtt_parser.add_argument("--device-id", required=True)
        if name == "publish":
            mqtt_parser.add_argument("--manifest", required=True, type=Path)
            mqtt_parser.add_argument("--signature", required=True, type=Path)
            mqtt_parser.add_argument("--base-url", required=True, type=_https_base)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    mqtt_factory=PublisherMqtt,
    server_factory=create_https_server,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "keys":
        generate_key_pair(args.private_key, args.public_key)
        print(json.dumps({"private_key": str(args.private_key), "public_key": str(args.public_key)}))
        return 0
    if args.command == "package":
        if args.expires_in <= 0:
            parser.error("--expires-in must be positive")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        bundle = build_release(
            args.payload,
            args.output,
            BundleRequest(
                device_id=args.device_id,
                version=args.version,
                entrypoint=args.entrypoint,
                artifact_base_url=args.base_url,
                private_key_path=args.private_key,
                job_id=str(uuid.uuid4()),
                created_at=now,
                expires_at=now + timedelta(seconds=args.expires_in),
            ),
        )
        print(
            json.dumps(
                {
                    "archive": str(bundle.archive_path),
                    "manifest": str(bundle.manifest_path),
                    "signature": str(bundle.signature_path),
                }
            )
        )
        return 0
    if args.command == "serve":
        server = server_factory(args.root, args.bind, args.port, args.cert, args.key)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    client = mqtt_factory(
        host=args.broker,
        port=args.port,
        ca_file=args.ca,
        cert_file=args.cert,
        key_file=args.key,
    )
    if args.command == "publish":
        try:
            manifest = ReleaseManifest.from_bytes(args.manifest.read_bytes())
            signature = args.signature.read_bytes()
        except OSError as exc:
            parser.error(f"cannot read release files: {exc.filename}")
        archive_name = Path(urlparse(manifest.artifact_url).path).name
        bundle = ReleaseBundle(
            args.manifest.parent / archive_name,
            args.manifest,
            args.signature,
            manifest,
        )
        if not signature:
            parser.error("signature file is empty")
        client.publish_command(args.device_id, build_command(bundle, args.base_url))
        print(json.dumps({"device_id": args.device_id, "job_id": manifest.job_id, "status": "published"}))
        return 0
    if args.command == "watch":
        client.start_status_watch(args.device_id, lambda status: print(json.dumps(status), flush=True))
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            client.close()
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
