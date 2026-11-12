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


def test_systemd_initialization_never_falls_back_to_updater_identity(tmp_path, monkeypatch):
    from capstone_ota.cluster import ClusterDevice
    marker = tmp_path / 'app-executed-as-updater'
    root = tmp_path / 'device'
    binary = root / 'slots/A/bin/app'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\ntouch ' + str(marker) + '\nexit 0\n')
    binary.chmod(0o755)
    # The protected tmp_path is intentionally inaccessible to the service account.
    device = ClusterDevice(root)
    monkeypatch.setattr(device, '_service', lambda _action: True)
    import pytest
    with pytest.raises(ValueError, match='initialization'):
        device._activate('A', {'systemd': True})
    assert not marker.exists()


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
    # This PC test substitutes both OS boundaries; hardware SEC-004 checks the real UID.
    monkeypatch.setattr(device, '_check_arguments', lambda app: [str(app), '--check'])
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


def test_progress_separates_historical_mismatch_from_successful_recovery(keys, payload, tmp_path):
    from capstone_ota.cluster import ClusterDevice
    for label, version in [('good', '2.0.0'), ('bad', '3.0.0')]:
        (payload / 'label').write_text(label)
        pack(payload, tmp_path / 'releases' / label, version, 2, keys[0])
    body = ('import json,sys;from pathlib import Path;'
            'ok=(Path(sys.argv[1])/"label").read_text()=="good";'
            'row={"expected":65 if ok else 62,"qml":65 if ok else 45,"model":65 if ok else 45,"valid":True,"passed":ok};'
            'print(json.dumps({"passed":ok,"observed":[row]}));sys.exit(0 if ok else 1)')
    root = tmp_path / 'device'; d = ClusterDevice(root, [sys.executable, '-c', body])
    with serving(tmp_path / 'releases') as url:
        d.update(url + '/good', keys[1])
        assert d.update(url + '/bad')['result'] == 'rolled_back'
    data = json.loads((root / 'progress.json').read_text())
    assert data['observation']['qml'] == data['observation']['expected'] == 65
    assert data['mismatch']['qml'] == 45 and data['mismatch']['expected'] == 62
    assert data['mismatch_slot'] == 'B' and data['mismatch_version'] == '3.0.0'
    assert data['verified_slot'] == data['slot_view']['selected'] == 'A'


def test_first_observed_mismatch_identity_is_not_taken_from_an_earlier_timeout(keys, payload, tmp_path):
    from capstone_ota.cluster import ClusterDevice
    for label, version in [('good','2.0.0'),('bad','3.0.0')]:
        (payload/'label').write_text(label)
        pack(payload,tmp_path/'releases'/label,version,2,keys[0])
    gate=tmp_path/'mode'; gate.write_text('baseline')
    body=('import json,sys;from pathlib import Path;'
          'label=(Path(sys.argv[1])/"label").read_text();'
          'baseline=Path('+repr(str(gate))+').read_text()=="baseline";'
          'r={"passed":True} if baseline else ({"passed":False,"error":"timeout"} if label=="bad" else '
          '{"passed":False,"observed":[{"expected":65,"qml":35,"passed":False}]});'
          'print(json.dumps(r));sys.exit(0 if r["passed"] else 1)')
    root=tmp_path/'device';d=ClusterDevice(root,[sys.executable,'-c',body])
    with serving(tmp_path/'releases') as url:
        d.update(url+'/good',keys[1]);gate.write_text('two-failures')
        with pytest.raises(ValueError,match='recovery failed'):d.update(url+'/bad')
    data=json.loads((root/'progress.json').read_text())
    assert data['mismatch']['qml']==35
    assert data['mismatch_slot']=='A' and data['mismatch_version']=='2.0.0'


def test_relay_can_select_ivi_without_changing_the_cluster():
    from capstone_ota.relay import target_command
    config = {'host': 'cockpit', 'checkout': '/runtime', 'python': '/runtime/python',
              'root': '/opt/capstone-ivi/device', 'public_key': '/keys/update.pub',
              'initialization_only': True, 'application': 'ivi'}
    assert 'ivi-update' in target_command(config)
    config['application'] = 'unknown'
    with pytest.raises(ValueError, match='application'):
        target_command(config)


def test_relay_adds_can_verdict_flags_only_when_both_fields_present():
    from capstone_ota.relay import target_command
    base = {'host': 'cockpit', 'checkout': '/runtime', 'python': '/runtime/python',
            'root': '/opt/capstone-ivi/device', 'public_key': '/keys/update.pub',
            'initialization_only': True}
    arguments = target_command({**base, 'can_channel': 'can0', 'can_secret_file': '/etc/capstone-ota/can-verdict.key'})
    assert arguments[-4:] == ['--verifier-can-channel', 'can0',
                              '--verifier-can-secret-file', '/etc/capstone-ota/can-verdict.key']
    assert '--verifier-can-channel' not in target_command(base)
    with pytest.raises(ValueError, match='invalid Cluster target configuration'):
        target_command({**base, 'can_channel': 'can0'})
    with pytest.raises(ValueError, match='can_secret_file must be an absolute path'):
        target_command({**base, 'can_channel': 'can0', 'can_secret_file': 'relative/path'})


def test_ivi_forward_deadline_covers_trial_and_recovery_checks(releases, keys, tmp_path, local_ssh, monkeypatch):
    from capstone_ota import relay
    from capstone_ota.ivi import IviDevice
    original = relay.exchange
    deadlines = []
    def observe_deadline(*args, **kwargs):
        deadlines.append(kwargs['timeout'])
        return original(*args, **kwargs)
    monkeypatch.setattr(relay, 'exchange', observe_deadline)
    config = {'host': 'cockpit', 'checkout': str(Path(__file__).resolve().parents[1]),
              'python': sys.executable, 'root': str(tmp_path / 'ivi'), 'public_key': str(keys[1]),
              'initialization_only': True, 'application': 'ivi'}
    with serving(releases) as origin:
        assert relay.forward(origin + '/v2', keys[1], config)['result'] == 'committed'
    assert deadlines[0] > 2 * IviDevice.verifier_timeout
