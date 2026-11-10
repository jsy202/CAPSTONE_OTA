"""Trusted bench checker for an already-running native Qt service.

The optional substitution flag is an explicit attack fixture, never an updater API.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time

from capstone_ota.bundle import extract, verify
from capstone_ota.device import Device
from capstone_ota.qt_verify import remote_command, remote_dashboard, verify as verify_display
from capstone_ota.process import exchange


def save_capture(directory, slot):
    """Never follow a screenshot symlink supplied by the app user as root."""
    fd = os.open('/run/capstone-cluster/capture.png', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not 8 <= metadata.st_size <= 5_000_000:
            raise ValueError('invalid screenshot file')
        data = stream.read(5_000_001)
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 5_000_000:
        raise ValueError('invalid screenshot content')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (Path(slot).name + '-' + str(time.monotonic_ns()) + '.png')
    path.write_bytes(data)
    return str(path)


def substitute_old(root, slot, old, public):
    root, slot, old = Path(root).resolve(), Path(slot).resolve(), Path(old)
    if not str(root).startswith('/opt/capstone-cluster/runs/') or root.name != 'device':
        raise ValueError('attack fixture is restricted to the CAN bench roots')
    device = Device(root)
    state = device._load()
    if state['phase'] != 'trial' or not state['committed'] or slot.parent != root / 'slots':
        raise ValueError('attack fixture requires a recorded recovery slot')
    target = device._slot(state['committed']['slot'])
    if target == slot or state['pending']['slot'] != slot.name:
        raise ValueError('attack fixture cannot replace the candidate')
    before = (root / 'state.json').read_bytes()
    metadata = verify((old / 'release.json').read_bytes(), (old / 'release.sig').read_bytes(), Path(public))
    if hashlib.sha256((old / 'app.tar.gz').read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('old archive digest mismatch')
    shutil.rmtree(target)
    extract(old / 'app.tar.gz', target)
    unchanged = before == (root / 'state.json').read_bytes()
    if not unchanged:
        raise ValueError('trusted journal changed during attack fixture')
    return {'old_signature_valid': True, 'trusted_journal_unchanged': True,
            'old_version': metadata['version']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--verifier', type=Path, help='Optional trusted third-board SSH peer configuration')
    parser.add_argument('--capture-dir', type=Path, required=True)
    parser.add_argument('--source', choices=['latest'], default='latest')
    parser.add_argument('--substitute-old', type=Path)
    parser.add_argument('--device-root', type=Path)
    parser.add_argument('--public-key', type=Path)
    parser.add_argument('slot', type=Path)
    args = parser.parse_args()
    try:
        if args.verifier:
            peer = json.loads(args.verifier.read_text())
            command = remote_command(peer, ['--remote', peer['peers'], '--source', args.source, str(args.slot)])
            # Detached masters outlive the verifier's per-request process-group cleanup.
            # These pinned aliases are provisioned by this bench, not supplied by apps.
            command[-1] = ('set -e; for host in capstone-source capstone-cluster-observer; do '
                           'ssh -O check "$host" 2>/dev/null || ssh -MNf "$host"; done; ' + command[-1])
            rows, _pid = exchange(command, [], timeout=50, allow_negative_verdict=True)
            if len(rows) != 1 or not isinstance(rows[0], dict) or type(rows[0].get('passed')) is not bool:
                raise ValueError('invalid third-board verification result')
            report = rows[0]
        else:
            with remote_dashboard(json.loads(args.config.read_text()), args.slot) as target:
                report = verify_display(target, args.source)
        try:
            report['capture'] = save_capture(args.capture_dir, args.slot)
        except (OSError, ValueError) as exc:
            report['capture_error'] = str(exc)
        if (args.substitute_old and report['passed']
                and (args.slot / 'bench-release.txt').read_text() == 'v3'):
            if not args.device_root or not args.public_key:
                raise ValueError('attack fixture needs an explicit root and public key')
            report['attack_fixture'] = substitute_old(args.device_root, args.slot, args.substitute_old, args.public_key)
            report.update(passed=False, error='ATTACK_FIXTURE_ROLLBACK_REQUEST')
        print(json.dumps(report))
        return 0 if report['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
