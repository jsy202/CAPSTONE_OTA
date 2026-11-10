"""IVI signed two-hop OTA security bench; no cluster replacement or CPU load is run."""
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
from dashboard.ivi.build import payload
from .run import serve

ROOT = Path(__file__).resolve().parents[1]


def attack_check(slot, root, old, public, evidence):
    """Authorized test fixture only; replace only this run's recorded recovery slot."""
    # Initialization is real; the negative trial verdict below is an explicit security-test fixture.
    checked = subprocess.run([str(Path(slot) / 'bin/app'), '--check'], capture_output=True, text=True, timeout=5)
    report = {'passed': checked.returncode == 0, 'observed': [], 'scope': 'IVI initialization; security fixture, not CPU verification'}
    if report['passed'] and json.loads((Path(slot) / 'ivi.json').read_text())['version'] == 'V3':
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
        report.update(passed=False, error='ATTACK_FIXTURE_ROLLBACK_REQUEST')
    return report


def run(output, scenario='all'):
    if scenario not in {'all', 'compatible', 'attack'}:
        raise ValueError('unknown scenario')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
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
    for label, version, svn in [('v1', '1.0.0', 1), ('v2', '2.0.0', 2), ('v3', '3.0.0', 2)]:
        app = payload(output / 'payloads' / label, 'V3' if label == 'v3' else 'V2')
        if label == 'v1':
            config = app / 'ivi.json'
            settings = json.loads(config.read_text()); settings['version'] = 'V1'
            config.write_text(json.dumps(settings))
            wrapper = app / 'bin/app'
            wrapper.write_text(wrapper.read_text().replace('exec python3',
                'touch ' + shlex.quote(str(old_marker)) + '\nexec python3'))
        pack(app, releases / label, version, svn, key)
    # This script substitutes SSH only on this demo's child environment, not global config.
    local_bin = output / 'local-transport'; local_bin.mkdir()
    shim = local_bin / 'ssh'
    shim.write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv("/bin/sh",["sh","-c",sys.argv[-1]])\n')
    shim.chmod(0o755)
    env = dict(os.environ, PATH=str(local_bin) + ':' + os.environ['PATH'])
    env['PYTHONPATH'] = str(ROOT) + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    def forward(origin, name, label, attack=False):
        root = output / (name + '-ivi')
        checker = [sys.executable, '-m', 'demo.ivi_attack', 'check', '--root', str(root),
                   '--old', str(releases / 'v1'), '--public', str(public),
                   '--evidence', str(output / 'attack-evidence.json')]
        # The non-attack path exercises the same real initialization without forcing rollback.
        if not attack:
            checker = [sys.executable, '-c', 'import json;print(json.dumps({"passed":True,"scope":"initialization-only security control; not CPU verification"}))']
        config = {'host': 'local-cockpit', 'checkout': str(ROOT), 'python': sys.executable,
                  'root': str(root), 'public_key': str(public), 'check_command': checker, 'application': 'ivi'}
        path = output / (name + '-target.json'); path.write_text(json.dumps(config))
        process = subprocess.run([sys.executable, '-m', 'capstone_ota', 'forward', origin + '/' + label,
                                  '--public-key', str(public), '--target', str(path)], env=env,
                                 cwd=ROOT, capture_output=True, text=True, timeout=90)
        if process.returncode not in (0, 1, 2) or not process.stdout:
            raise ValueError('relay did not return target result: ' + process.stderr[-1000:])
        return json.loads(process.stdout)
    result = {'transport': 'real HTTP two hops; local SSH substitute',
              'scope': 'IVI recovery security fixture; fixed cluster untouched; CPU scenario not run'}
    with serve(releases) as origin:
        for name in ('compatible', 'attack'):
            if scenario not in {'all', name}:
                continue
            options = {'attack': name == 'attack'}
            first = forward(origin, name, 'v2', **options)
            assert first['result'] == 'committed', first
            result[name] = forward(origin, name, 'v3', **options)
    if 'compatible' in result:
        assert result['compatible']['result'] == 'committed', result['compatible']
    if 'attack' in result:
        evidence = json.loads((output / 'attack-evidence.json').read_text())
        evidence['old_app_executed'] = old_marker.exists()
        result['attack_evidence'] = evidence
        assert result['attack']['result'] == 'recovery_failed' and evidence['old_app_executed'] is False, result['attack']
    (output / 'result.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    demo = sub.add_parser('run'); demo.add_argument('--output', required=True, type=Path)
    attack = sub.add_parser('check')
    for option in ('root', 'old', 'public', 'evidence'):
        attack.add_argument('--' + option, required=True, type=Path)
    attack.add_argument('slot', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'check':
            result = attack_check(args.slot, args.root, args.old, args.public, args.evidence)
            print(json.dumps(result))
            return 0 if result['passed'] else 1
        result = run(args.output)
        print(json.dumps({name: result[name]['result'] for name in ('compatible', 'attack')}))
        print('Evidence:', args.output / 'result.json')
        return 0
    except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
