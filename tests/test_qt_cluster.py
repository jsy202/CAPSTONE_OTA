"""Run the actual Volvo Qt models/QML; the transport is explicitly loopback emulation."""
import importlib
from pathlib import Path
import pytest
from dashboard.qt_cluster.build import default_payload, payload as prepare_payload
from test_cluster_relay import local_ssh

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / '.demo/qt-build/capstone-qt-cluster').exists(),
                                reason='build the actual Qt dashboard first; see docs/QT_CLUSTER.md')


@pytest.fixture(autouse=True)
def qt_runtime(monkeypatch):
    import json
    config = ROOT / '.demo/qt-build/runtime.json'
    if config.exists():
        prefix = json.loads(config.read_text())['qt_prefix']
        if prefix:
            monkeypatch.setenv('CAPSTONE_QT_PREFIX', prefix)


def qt():
    return importlib.import_module('capstone_ota.qt_verify')


@pytest.mark.parametrize('version', ['V1', 'V2', 'V3'])
def test_actual_dashboard_displays_its_packaged_version(tmp_path, version):
    slot = prepare_payload(ROOT / '.demo/qt-build/capstone-qt-cluster', tmp_path / version, 'source_counter')
    import json
    config = slot / 'cluster.json'
    settings = json.loads(config.read_text())
    settings['version'] = version
    config.write_text(json.dumps(settings))
    with qt().running_dashboard(slot) as target:
        assert qt().snapshot(target)['version_label'] == version


def test_readonly_qt_initialization_is_stable_without_accessing_user_display():
    import json,os,subprocess
    from capstone_ota.bundle import tree_hash
    slot = default_payload('source_counter')
    before = tree_hash(slot)
    environment = dict(os.environ, DISPLAY=':65530', QT_QPA_PLATFORM='xcb')
    for _ in range(12):
        result = subprocess.run([str(slot / 'bin/app'), '--check'], env=environment,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['initialized'] is True
    assert tree_hash(slot) == before


def test_actual_qt_models_follow_vehicle_inputs_and_ignore_delayed_samples():
    q = qt()
    payload = default_payload('source_counter')
    with q.running_dashboard(payload) as target:
        first = q.deliver(target, speed=600, rpm=2200, gear=3, warnings=1, counter=250)
        assert first['model_speed'] == first['qml_speed'] == 60.0
        assert first['rpm'] == 2200
        assert first['gear'] == 3
        assert first['warning'] is True
        q.deliver(target, speed=620, rpm=2300, gear=3, warnings=0, counter=251)
        stale = q.deliver(target, speed=350, rpm=1200, gear=2, warnings=0, counter=249)
        assert stale['model_speed'] == stale['qml_speed'] == 62.0
        assert stale['source_counter'] == 251
        assert stale['valid'] is True


def test_actual_qt_counter_wrap_timeout_and_recovery():
    import time
    q = qt()
    with q.running_dashboard(default_payload('source_counter')) as target:
        q.deliver(target, speed=600, counter=255)
        wrapped = q.deliver(target, speed=610, counter=0)
        assert wrapped['model_speed'] == 61.0
        time.sleep(0.65)
        assert q.snapshot(target)['valid'] is False
        recovered = q.deliver(target, speed=620, counter=1)
        assert recovered['valid'] is True
        assert recovered['qml_speed'] == 62.0


def test_actual_qt_mixed_version_failure_is_observed_not_fabricated():
    q = qt()
    with q.running_dashboard(default_payload('arrival_order')) as target:
        q.deliver(target, speed=600, counter=250)
        q.deliver(target, speed=620, counter=251)
        stale = q.deliver(target, speed=350, counter=249)
        assert stale['model_speed'] == stale['qml_speed'] == 35.0
        assert stale['pid'] != __import__('os').getpid()


def test_invalid_crc_does_not_change_qt_display():
    q = qt()
    with q.running_dashboard(default_payload('source_counter')) as target:
        q.deliver(target, speed=600, counter=250)
        report = q.deliver(target, speed=2000, counter=251, corrupt=True)
        assert report['qml_speed'] == 60.0
        assert report['source_counter'] == 250


def test_snapshot_observer_runs_as_separate_process_and_binds_candidate_identity():
    import sys
    q = qt()
    with q.running_dashboard(default_payload('source_counter')) as target:
        target['observer_command'] = [sys.executable, '-m', 'capstone_ota.qt_verify',
                                      'snapshot', '--socket', target['socket']]
        assert q.deliver(target, speed=600, counter=1)['qml_speed'] == 60.0
        observer = target['observer_command']
        target['observer_command'] = [sys.executable, '-c', 'raise SystemExit(1)']
        with pytest.raises(ValueError):
            q.snapshot(target)
        target['observer_command'] = observer
        target['pid'] += 1
        with pytest.raises(ValueError, match='identity'):
            q.snapshot(target)


def test_third_role_remote_gate_drives_source_and_observes_existing_qt(tmp_path, local_ssh):
    import sys,json,subprocess
    q = qt()
    slot = default_payload('source_counter')
    with q.running_dashboard(slot) as target:
        peer = {'host': 'cluster-test', 'checkout': str(ROOT), 'python': sys.executable}
        peers = tmp_path / 'peers.json'
        peers.write_text(json.dumps({'cluster': dict(peer, socket=target['socket']),
                                     'central': dict(peer), 'emulated_udp': True}))
        gate = tmp_path / 'gate.json'
        gate.write_text(json.dumps(dict(peer, peers=str(peers))))
        process = subprocess.run([sys.executable, '-m', 'capstone_ota.qt_verify', 'remote-gate',
                                  '--config', str(gate), str(slot)], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout + process.stderr
        report = json.loads(process.stdout)
        assert report['passed'] is True
        assert report['pid'] == target['pid']


@pytest.mark.parametrize('policy,source,expected', [('source_counter', 'buffered', True),
                                                   ('arrival_order', 'latest', True),
                                                   ('arrival_order', 'buffered', False)])
def test_version_combination_matrix_with_real_qt_fault_injection(policy, source, expected):
    q = qt()
    with q.running_dashboard(default_payload(policy)) as target:
        report = q.verify(target, source=source, cycles=2)
    assert report['passed'] is expected
    assert report['timeout_detected'] is True
    assert report['cycles'] == 2
    if source == 'buffered':
        originals = {240: 40.0, 241: 45.0, 242: 60.0, 243: 62.0, 244: 65.0, 245: 62.0, 246: 65.0}
        for row in report['observed']:
            assert row['input_speed'] == originals[row['counter']]


def test_unrelated_bus_identifier_does_not_advance_vehicle_evidence():
    import time
    from capstone_ota.qt_source import send
    q = qt()
    with q.running_dashboard(default_payload('source_counter')) as target:
        before = q.deliver(target, speed=600, counter=1)
        send({'id': 0x201, 'data': '0000000000000000'}, udp_port=target['port'])
        time.sleep(.05)
        assert q.snapshot(target)['generation'] == before['generation']
        assert q.deliver(target, speed=610, counter=2)['qml_speed'] == 61.0


def test_qt_signed_relay_rolls_back_and_rejects_authentic_old_recovery(tmp_path):
    runner = importlib.import_module('demo.qt_run')
    result = runner.run(tmp_path / 'qt-chain')
    assert result['compatible']['result'] == 'committed'
    assert result['compatible']['version'] == '3.0.0'
    assert result['mixed']['result'] == 'rolled_back'
    assert result['mixed']['version'] == '2.0.0'
    assert [r['passed'] for r in result['mixed']['verification']] == [False, True]
    assert result['attack']['result'] == 'recovery_failed'
    assert result['attack']['min_svn'] == 2
    assert result['attack_evidence']['old_signature_valid'] is True
    assert result['attack_evidence']['trusted_journal_unchanged'] is True
    assert result['attack_evidence']['old_app_executed'] is False
