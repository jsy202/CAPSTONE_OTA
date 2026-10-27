"""Real signed releases, two HTTP hops and a separate target process; SSH is substituted locally."""
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest

from capstone_ota.bundle import pack, extract
from test_device import keys, payload, releases, serving


def cli(root, *arguments):
    return [sys.executable, '-m', 'capstone_ota', '--root', str(root), 'cluster-update', *arguments]


def test_cluster_cli_installs_only_in_target_root_and_requires_explicit_validation(releases, keys, tmp_path):
    root = tmp_path / 'cluster'
    with serving(releases) as url:
        ambiguous = subprocess.run(cli(root, url + '/v2', '--public-key', str(keys[1])), capture_output=True, text=True)
        assert ambiguous.returncode != 0
        installed = subprocess.run(cli(root, url + '/v2', '--public-key', str(keys[1]), '--initialization-only'),
                                   capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    report = json.loads(installed.stdout)
    assert report['version'] == '1.1.0'
    assert report['validation'] == 'initialization_only'
    assert (root / 'current/bin/app').is_file()
    assert not (tmp_path / 'central/current').exists()


def test_empty_external_check_argument_cannot_silently_install_without_validation(releases, keys, tmp_path):
    root = tmp_path / 'cluster'
    with serving(releases) as url:
        result = subprocess.run(cli(root, url + '/v2', '--public-key', str(keys[1]), '--check-command', ''),
                                capture_output=True, text=True)
    assert result.returncode != 0
    assert not (root / 'current').exists()


def test_external_checker_rejects_candidate_and_rechecks_previous_on_rollback(keys, payload, tmp_path):
    cluster = importlib.import_module('capstone_ota.cluster')
    (payload / 'label').write_text('good')
    pack(payload, tmp_path / 'releases/good', '2.0.0', 2, keys[0])
    (payload / 'label').write_text('bad')
    pack(payload, tmp_path / 'releases/bad', '3.0.0', 2, keys[0])
    checker = tmp_path / 'trusted-check.py'
    checker.write_text('import json,sys\nfrom pathlib import Path\n'
                       'ok=(Path(sys.argv[1])/"label").read_text()=="good"\n'
                       'print(json.dumps({"passed":ok}))\nraise SystemExit(0 if ok else 1)\n')
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, str(checker)])
    with serving(tmp_path / 'releases') as url:
        device.update(url + '/good', keys[1])
        result = device.update(url + '/bad')
    assert result['result'] == 'rolled_back'
    assert result['version'] == '2.0.0'
    assert [r['passed'] for r in device.reports] == [True, False, True]


@pytest.mark.parametrize('body', ['print("{}")', 'print(\'{"passed":true}\');raise SystemExit(1)',
                                  'print(\'{"passed":false}\')'])
def test_target_never_commits_without_successful_explicit_external_verdict(releases, keys, tmp_path, body):
    cluster = importlib.import_module('capstone_ota.cluster')
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, '-c', body])
    with serving(releases) as url:
        result = device.update(url + '/v2', keys[1])
    assert result['result'] == 'failed'
    assert result['version'] is None


def test_target_recovery_blocks_authentic_v1_substitution_before_execution(releases, keys, tmp_path):
    cluster = importlib.import_module('capstone_ota.cluster')
    device = cluster.ClusterDevice(tmp_path / 'cluster')
    with serving(releases) as url:
        installed = device.update(url + '/v2', keys[1])
    # Model an interrupted trial with an intact protected record, then swap only recovery files.
    state = device._load()
    state.update(phase='trial', pending=dict(state['committed'], slot='B'))
    device._save(state)
    # Existing generic v1/v2 fixture has identical payload bytes; use a genuinely different old app.
    marker = tmp_path / 'executed-old-app'
    old = tmp_path / 'old-payload/bin'
    old.mkdir(parents=True)
    (old / 'app').write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    (old / 'app').chmod(0o755)
    pack(old.parent, tmp_path / 'old-release', '1.0.0', 1, keys[0])
    recovery = device.root / 'slots' / installed['active_slot']
    shutil.rmtree(recovery)
    extract(tmp_path / 'old-release/app.tar.gz', recovery)
    with pytest.raises(ValueError, match='recovery failed'):
        device.update()
    assert device.status()['result'] == 'recovery_failed'
    assert device.status()['min_svn'] == 2
    assert not marker.exists()


@pytest.fixture
def local_ssh(tmp_path, monkeypatch):
    """Replace only SSH transport; execute the real quoted remote command in another process."""
    import os
    binary = tmp_path / 'test-bin'
    binary.mkdir()
    ssh = binary / 'ssh'
    ssh.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                   'os.execv("/bin/sh",["sh","-c",sys.argv[-1]])\n')
    ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary) + ':' + os.environ['PATH'])


def target_config(root, public):
    return {'host': 'cluster-test', 'checkout': str(Path(__file__).parents[1]),
            'python': sys.executable, 'root': str(root), 'public_key': str(public),
            'initialization_only': True}


def test_forward_cli_really_installs_on_cluster_and_preserves_rollback_result(releases, keys, tmp_path, local_ssh):
    root = tmp_path / 'cluster'
    central = tmp_path / 'central'
    central.mkdir()
    (central / 'untouched').write_text('Central application is not the update target')
    config = tmp_path / 'target.json'
    config.write_text(json.dumps(target_config(root, keys[1])))
    command = [sys.executable, '-m', 'capstone_ota', '--root', str(central), 'forward',
               '--public-key', str(keys[1]), '--target', str(config)]
    with serving(releases) as origin:
        installed = subprocess.run([*command, origin + '/v2'], capture_output=True, text=True)
        assert installed.returncode == 0, installed.stderr
        assert json.loads(installed.stdout)['target'] == 'cluster'
        failed = subprocess.run([*command, origin + '/bad'], capture_output=True, text=True)
    assert failed.returncode == 2, failed.stderr
    report = json.loads(failed.stdout)
    assert report['result'] == 'rolled_back'
    assert report['version'] == '1.1.0'
    assert report['min_svn'] == 2
    assert list(central.iterdir()) == [central / 'untouched']
    assert (root / 'current/bin/app').is_file()


def test_forward_external_gate_receives_target_slot_and_rechecks_rollback(keys, payload, tmp_path, local_ssh):
    relay = importlib.import_module('capstone_ota.relay')
    for label, version in [('good', '2.0.0'), ('bad', '3.0.0')]:
        (payload / 'label').write_text(label)
        pack(payload, tmp_path / 'releases' / label, version, 2, keys[0])
    body = ('import json,sys;from pathlib import Path;'
            'ok=(Path(sys.argv[1])/"label").read_text()=="good";'
            'print(json.dumps({"passed":ok}));sys.exit(0 if ok else 1)')
    config = target_config(tmp_path / 'cluster', keys[1])
    config.pop('initialization_only')
    config['check_command'] = [sys.executable, '-c', body]
    with serving(tmp_path / 'releases') as origin:
        installed = relay.forward(origin + '/good', keys[1], config)
        rolled_back = relay.forward(origin + '/bad', keys[1], config)
    assert installed['validation'] == 'external'
    assert rolled_back['result'] == 'rolled_back'
    assert rolled_back['version'] == '2.0.0'
    assert [row['passed'] for row in rolled_back['verification']] == [False, True]


def test_external_observer_runs_after_cluster_service_activation(releases, keys, tmp_path, monkeypatch):
    cluster = importlib.import_module('capstone_ota.cluster')
    active = tmp_path / 'service-active'
    # Only the OS service boundary is substituted; observer is an actual separate process.
    body = f'import json;from pathlib import Path;print(json.dumps({{"passed":Path({str(active)!r}).exists()}}))'
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, '-c', body])
    def service(action):
        if action == 'restart':
            active.touch()
        return True
    monkeypatch.setattr(device, '_service', service)
    with serving(releases) as origin:
        result = device.update(origin + '/v2', keys[1], systemd=True)
    assert result['result'] == 'committed'
    assert device.reports == [{'passed': True}]


def test_forward_lost_reply_reports_uncertainty_not_success(releases, keys, tmp_path, monkeypatch):
    import os
    relay = importlib.import_module('capstone_ota.relay')
    binary = tmp_path / 'test-bin'
    binary.mkdir()
    ssh = binary / 'ssh'
    ssh.write_text('#!/usr/bin/env python3\nimport subprocess,sys\n'
                   'subprocess.run(["sh","-c",sys.argv[-1]],stdout=subprocess.DEVNULL)\n'
                   'raise SystemExit(255)\n')
    ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary) + ':' + os.environ['PATH'])
    root = tmp_path / 'cluster'
    with serving(releases) as origin:
        with pytest.raises(ValueError, match='outcome unconfirmed'):
            relay.forward(origin + '/v2', keys[1], target_config(root, keys[1]))
    from capstone_ota.device import Device
    assert Device(root).status()['result'] == 'committed'


def test_forward_reports_target_recovery_refusal_as_known_failure(releases, keys, tmp_path, local_ssh):
    from capstone_ota.device import Device
    relay = importlib.import_module('capstone_ota.relay')
    root = tmp_path / 'cluster'
    target = Device(root)
    with serving(releases) as origin:
        target.update(origin + '/v2', keys[1])
        state = target._load()
        shutil.copytree(root / 'slots/A', root / 'slots/B')
        state.update(phase='trial', pending=dict(state['committed'], slot='B'))
        target._save(state)
        target._select('B')
        (root / 'slots/A/config.json').write_text('substituted recovery')
        result = relay.forward(origin + '/v3', keys[1], target_config(root, keys[1]))
    assert result['result'] == 'recovery_failed'
    assert result['passed'] is False
    assert result['min_svn'] == 2


def test_forward_rejects_status_with_wrong_committed_security_floor(releases, keys, tmp_path, monkeypatch):
    import os
    relay = importlib.import_module('capstone_ota.relay')
    binary = tmp_path / 'test-bin'
    binary.mkdir()
    ssh = binary / 'ssh'
    ssh.write_text('#!/usr/bin/env python3\nimport json\nprint(json.dumps('
                   '{"phase":"stable","result":"committed","version":"1.1.0",'
                   '"active_slot":"A","trial_version":None,"min_svn":1,'
                   '"validation":"initialization_only"}))\n')
    ssh.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary) + ':' + os.environ['PATH'])
    with serving(releases) as origin:
        with pytest.raises(ValueError, match='outcome unconfirmed'):
            relay.forward(origin + '/v2', keys[1], target_config(tmp_path / 'cluster', keys[1]))


@pytest.mark.parametrize('change', [{'host': '-oProxyCommand=bad'}, {'initialization_only': False},
                                  {'initialization_only': True, 'check_command': ['python']},
                                  {'python': 'python3'}])
def test_invalid_target_configuration_is_rejected_before_transfer(keys, tmp_path, change):
    relay = importlib.import_module('capstone_ota.relay')
    config = dict(target_config(tmp_path / 'cluster', keys[1]), **change)
    with pytest.raises(ValueError):
        relay.forward('http://127.0.0.1:1/not-contacted', keys[1], config)


def test_verified_cache_survives_origin_shutdown_and_target_downloads_second_hop(releases, keys, tmp_path):
    relay = importlib.import_module('capstone_ota.relay')
    with serving(releases) as origin:
        cache = relay.cache_release(origin + '/v2', keys[1], tmp_path / 'cache')
    root = tmp_path / 'cluster'
    with relay.serving_release(cache) as second_hop:
        process = subprocess.run(cli(root, second_hop, '--public-key', str(keys[1]), '--initialization-only'),
                                 capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        for suffix in ['/', '/trusted.pub', '/app.tar.gz/../release.json', '/release.json?x=1']:
            with pytest.raises(HTTPError) as denied:
                urlopen(second_hop + suffix, timeout=2)
            assert denied.value.code == 404
    assert json.loads(process.stdout)['version'] == '1.1.0'
    assert not (tmp_path / 'cache/current').exists()


def test_corrupt_release_is_not_forwarded(releases, keys, tmp_path):
    relay = importlib.import_module('capstone_ota.relay')
    with (releases / 'v2/app.tar.gz').open('ab') as stream:
        stream.write(b'tampered')
    with serving(releases) as origin:
        with pytest.raises(ValueError, match='hash'):
            relay.cache_release(origin + '/v2', keys[1], tmp_path / 'cache')


def test_target_independently_rejects_wrong_pinned_key(releases, keys, tmp_path):
    relay = importlib.import_module('capstone_ota.relay')
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    wrong = tmp_path / 'wrong.pub'
    wrong.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with serving(releases) as origin:
        cache = relay.cache_release(origin + '/v2', keys[1], tmp_path / 'cache')
    with relay.serving_release(cache) as url:
        process = subprocess.run(cli(tmp_path / 'cluster', url, '--public-key', str(wrong), '--initialization-only'),
                                 capture_output=True, text=True)
    assert process.returncode != 0
    assert not (tmp_path / 'cluster/current').exists()
