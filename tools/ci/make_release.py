"""Build one signed IVI release for CI.

The private key is read from the OTA_SIGNING_KEY environment variable (PEM text)
or --key-file, written to a 0600 temp file only while signing, and never stored in
the repository. The release is verified again with the matching public key before
it is reported as built, and a build-info.json records which commit produced it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from capstone_ota.bundle import pack, tree_hash, verify
from dashboard.ivi.build import payload

ROOT = Path(__file__).resolve().parents[2]
FILES = ('release.json', 'release.sig', 'app.tar.gz')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def commit_id() -> str:
    if os.environ.get('GITHUB_SHA'):
        return os.environ['GITHUB_SHA']
    try:
        return subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def build(version: str, output: Path, key_file: Path, versions_file: Path, map_file: Path | None = None) -> dict:
    versions = json.loads(Path(versions_file).read_text())
    if version not in versions:
        raise ValueError(f'version {version!r} is not in {versions_file}; known: {sorted(versions)}')
    label, svn = versions[version]['label'], versions[version]['svn']
    output = Path(output).absolute()
    staging = output / '_payload'
    payload(staging, label, map_file=map_file)
    release = output / 'release'
    pack(staging, release, version, svn, key_file)

    private = serialization.load_pem_private_key(Path(key_file).read_bytes(), password=None)
    public_pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo)
    public_path = release / 'release-public.pem'
    public_path.write_bytes(public_pem)
    # A release that does not verify with its own public key must never be published.
    metadata = verify((release / 'release.json').read_bytes(), (release / 'release.sig').read_bytes(), public_path)
    if metadata['sha256'] != sha256(release / 'app.tar.gz'):
        raise ValueError('release.json hash does not match app.tar.gz')

    info = {
        'version': version, 'svn': svn, 'label': label,
        'commit': commit_id(),
        'payload_tree_sha256': tree_hash(staging),
        'public_key_sha256': hashlib.sha256(public_pem).hexdigest(),
        'files': {name: sha256(release / name) for name in FILES},
        'workflow_run': os.environ.get('GITHUB_RUN_ID', ''),
        'verified_with_own_public_key': True,
    }
    (release / 'build-info.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    (release / 'SHA256SUMS').write_text(''.join(f'{info["files"][n]}  {n}\n' for n in FILES))
    return info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, help='IVI version, e.g. 3.0.0 (must exist in versions file)')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--versions-file', type=Path, default=ROOT / 'release/versions.json')
    parser.add_argument('--key-file', type=Path, help='Ed25519 PEM private key; default: OTA_SIGNING_KEY env var')
    parser.add_argument('--map', type=Path, help='map.json to ship in map-enabled releases')
    args = parser.parse_args(argv)
    temp = None
    try:
        key_file = args.key_file
        if key_file is None:
            pem = os.environ.get('OTA_SIGNING_KEY')
            if not pem:
                print('error: set OTA_SIGNING_KEY or pass --key-file', file=sys.stderr)
                return 2
            handle, name = tempfile.mkstemp(suffix='.pem')
            temp = Path(name)
            os.chmod(name, 0o600)
            with os.fdopen(handle, 'w') as stream:
                stream.write(pem if pem.endswith('\n') else pem + '\n')
            key_file = temp
        info = build(args.version, args.output, key_file, args.versions_file, args.map)
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
