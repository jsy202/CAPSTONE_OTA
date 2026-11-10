"""Create a throwaway signing key and signed V2/V3 mock-IVI releases (private key stays local)."""
from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from capstone_ota.bundle import pack
from dashboard.ivi.build import payload

RELEASES = {'v2': ('V2', '2.0.0', 1), 'v3': ('V3', '3.0.0', 2)}


def make(output: Path, map_file: Path | None = None) -> dict:
    output = Path(output).absolute()
    keys = output / 'keys'
    keys.mkdir(parents=True, exist_ok=True)
    private_path, public_path = keys / 'ivi-private.pem', keys / 'ivi-public.pem'
    if not private_path.exists():
        key = Ed25519PrivateKey.generate()
        private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
        private_path.chmod(0o600)
        public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo))
    releases = {}
    for name, (label, version, svn) in RELEASES.items():
        target = output / 'releases' / name
        if target.exists():
            raise ValueError(f'{target} exists; choose a new output directory')
        staging = output / 'payloads' / name
        payload(staging, label, map_file=map_file)
        releases[name] = pack(staging, target, version, svn, private_path)
    return {'public_key': str(public_path), 'releases': releases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--map', type=Path, help='map.json to ship inside each release')
    args = parser.parse_args()
    print(make(args.output, args.map))
