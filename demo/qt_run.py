"""Real Volvo Qt + signed two-hop OTA; local SSH execution substitute, no CAN hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from capstone_ota.bundle import pack, verify, extract
from capstone_ota.qt_verify import check_slot
from dashboard.qt_cluster.build import payload, ROOT
from .run import serve


def attack_check(slot, root, old, public, evidence):
    """Authorized test fixture only; replace only this run's recorded recovery slot."""
    report = check_slot(slot, capture_dir=Path(evidence).parent / 'screenshots')
    if report.get('error') == 'STALE_REPLAY_DISPLAY_MISMATCH':
        root, slot, old = Path(root).resolve(), Path(slot).resolve(), Path(old)
        journal = root / 'state.json'
        before = journal.read_bytes()
        state = json.loads(before)
        target = root / 'slots' / state['committed']['slot']
        if (slot.parent != root / 'slots' or target == slot or target.is_symlink()
                or target.parent.is_symlink() or state['phase'] != 'trial'):
            raise ValueError('attack fixture target must be this bench recovery slot')
        metadata = verify((old / 'release.json').read_bytes(), (old / 'release.sig').read_bytes(), public)
        if hashlib.sha256((old / 'app.tar.gz').read_bytes()).hexdigest() != metadata['sha256']:
            raise ValueError('old release archive is not authentic')
        shutil.rmtree(target)
        extract(old / 'app.tar.gz', target)
        Path(evidence).write_text(json.dumps({'old_signature_valid': True,
            'trusted_journal_unchanged': before == journal.read_bytes(), 'old_version': metadata['version']}))
    return report


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = ROOT / '.demo/qt-build/capstone-qt-cluster'
    if not binary.is_file():
        raise ValueError('build dashboard/qt_cluster first')
    keys = output / 'keys'; keys.mkdir(mode=0o700)
    private = Ed25519PrivateKey.generate()
    key, public = keys / 'update.key', keys / 'update.pub'
    key.touch(mode=0o600)
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    releases = output / 'releases'
    old_marker = output / 'old-app-executed'
    for label, version, svn, policy in [('v1', '1.0.0', 1, 'arrival_order'),
                                       ('v2', '2.0.0', 2, 'source_counter'),
                                       ('v3', '3.0.0', 2, 'arrival_order')]:
        app = payload(binary, output / 'payloads' / label, policy, label.upper())
        (app / 'release.txt').write_text(label)
        if label == 'v1':
            wrapper = app / 'bin/app'
            wrapper.write_text(wrapper.read_text().replace('exec "$task_root/libexec/dashboard"',
                'touch ' + shlex.quote(str(old_marker)) + '\nexec "$task_root/libexec/dashboard"'))
        pack(app, releases / label, version, svn, key)
    # This script substitutes SSH only on this demo's child environment, not global config.
    local_bin = output / 'local-transport'; local_bin.mkdir()
    shim = local_bin / 'ssh'
    shim.write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv("/bin/sh",["sh","-c",sys.argv[-1]])\n')
    shim.chmod(0o755)
    env = dict(os.environ, PATH=str(local_bin) + ':' + os.environ['PATH'])
    env['PYTHONPATH'] = str(ROOT) + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    captures = output / 'screenshots'
    def forward(origin, name, label, source='buffered', attack=False):
        root = output / (name + '-cluster')
        checker = [sys.executable, '-m', 'capstone_ota.qt_verify', '--source', source,
                   '--capture-dir', str(captures)]
        if attack:
            checker = [sys.executable, '-m', 'demo.qt_run', 'attack-check', '--root', str(root),
                       '--old', str(releases / 'v1'), '--public', str(public),
                       '--evidence', str(output / 'attack-evidence.json')]
        config = {'host': 'local-cluster', 'checkout': str(ROOT), 'python': sys.executable,
                  'root': str(root), 'public_key': str(public), 'check_command': checker}
        path = output / (name + '-target.json'); path.write_text(json.dumps(config))
        process = subprocess.run([sys.executable, '-m', 'capstone_ota', 'forward', origin + '/' + label,
                                  '--public-key', str(public), '--target', str(path)], env=env,
                                 cwd=ROOT, capture_output=True, text=True, timeout=90)
        if process.returncode not in (0, 1, 2) or not process.stdout:
            raise ValueError('relay did not return target result: ' + process.stderr[-1000:])
        return json.loads(process.stdout)
    with serve(releases) as origin:
        first = forward(origin, 'compatible', 'v2', source='latest')
        assert first['result'] == 'committed', first
        compatible = forward(origin, 'compatible', 'v3', source='latest')
        first = forward(origin, 'mixed', 'v2')
        assert first['result'] == 'committed', first
        mixed = forward(origin, 'mixed', 'v3')
        first = forward(origin, 'attack', 'v2', attack=True)
        assert first['result'] == 'committed', first
        attack = forward(origin, 'attack', 'v3', attack=True)
    evidence = json.loads((output / 'attack-evidence.json').read_text())
    evidence['old_app_executed'] = old_marker.exists()
    result = {'transport': 'real HTTP two hops; local SSH substitute; loopback UDP CAN emulation',
              'ui': 'pinned Volvo Qt models and original QML, plus CAPSTONE telemetry overlay',
              'compatible': compatible, 'mixed': mixed, 'attack': attack, 'attack_evidence': evidence}
    assert compatible['result'] == 'committed', compatible
    assert mixed['result'] == 'rolled_back' and mixed['version'] == '2.0.0', mixed
    assert attack['result'] == 'recovery_failed' and evidence['old_app_executed'] is False, attack
    (output / 'result.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    demo = sub.add_parser('run'); demo.add_argument('--output', required=True, type=Path)
    attack = sub.add_parser('attack-check')
    for option in ('root', 'old', 'public', 'evidence'):
        attack.add_argument('--' + option, required=True, type=Path)
    attack.add_argument('slot', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'attack-check':
            result = attack_check(args.slot, args.root, args.old, args.public, args.evidence)
            print(json.dumps(result))
            return 0 if result['passed'] else 1
        result = run(args.output)
        print(json.dumps({name: result[name]['result'] for name in ('compatible', 'mixed', 'attack')}))
        print('Evidence:', args.output / 'result.json')
        return 0
    except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
