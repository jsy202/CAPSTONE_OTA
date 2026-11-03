import pytest

pytestmark = pytest.mark.hardware


def test_CAN_001_actual_two_way_frames(physical_bench):
    report = physical_bench.two_way()
    physical_bench.record('CAN-001', report)
    assert report['passed'] is True


@pytest.mark.parametrize('profile,expected,version', [
    ('compatible', 'committed', '3.0.0'),
    ('mixed', 'rolled_back', '2.0.0'),
    ('attack', 'recovery_failed', '2.0.0'),
])
def test_OTA_CAN_002_real_service_update_and_recovery(physical_bench, profile, expected, version):
    report = physical_bench.scenario(profile)
    physical_bench.record('OTA-CAN-002-' + profile, report)
    status = report['candidate']['status']
    assert status['result'] == expected
    assert status['version'] == version
    assert status['validation'] == 'external'
    assert all(v.get('transport', '').startswith('SocketCAN') for v in status['verification'])
    if physical_bench.verifier:
        assert all(v.get('transport') == 'SocketCAN directly observed by verifier' for v in status['verification'])
    if profile == 'mixed':
        assert [v['passed'] for v in status['verification']] == [False, True]
        failure = next(r for r in status['verification'][0]['observed'] if not r['passed'])
        assert failure['expected'] == 62 and failure['qml'] == failure['model'] == 45
        view = report['progress']
        assert view['mismatch']['qml'] == 45 and view['observation']['qml'] == 65
        assert view['verified_slot'] == view['slot_view']['selected'] == 'A'
        assert view['slot_view']['B']['version'] == '3.0.0'
    if profile == 'attack':
        assert status['min_svn'] == 2
        assert report['old_app_executed'] is False
        fixture = status['verification'][0]['attack_fixture']
        assert fixture['old_signature_valid'] and fixture['trusted_journal_unchanged']
        view = report['progress']
        assert view['slot_view']['A']['role'] == 'recovery_failed'
        assert view['slot_view']['selected'] == 'B' and view['verified_slot'] is None
        assert view['injected_recovery_version'] == '1.0.0'


def test_CAN_003_crc_wrap_timeout_and_signal_models(physical_bench):
    report = physical_bench.signals()
    physical_bench.record('CAN-003', report)
    assert report['passed'] is True


def test_CAN_004_unrelated_traffic_with_measured_load(physical_bench):
    report = physical_bench.load()
    physical_bench.record('CAN-004', report)
    assert report['verification']['passed'] is True
    assert report['load_tool'] and report['load_active_before_and_after'] is True


def test_SEC_003_missing_evidence_cannot_commit(physical_bench):
    report = physical_bench.scenario('missing-evidence')
    physical_bench.record('SEC-003', report)
    assert report['candidate']['status']['result'] == 'recovery_failed'
    assert report['candidate']['status']['min_svn'] == 2
    assert report['candidate']['status']['version'] == '2.0.0'


def test_SEC_004_app_initialization_is_not_root(physical_bench):
    report = physical_bench.initialization_identity()
    physical_bench.record('SEC-004', report)
    assert report['uids'] and all(uid == report['app_uid'] != 0 for uid in report['uids'])


def test_SEC_005_missing_third_board_can_witness_cannot_commit(physical_bench):
    if not physical_bench.verifier:
        pytest.skip('third-board witness not selected')
    report = physical_bench.scenario('missing-witness')
    physical_bench.record('SEC-005', report)
    assert report['candidate']['status']['result'] == 'recovery_failed'
    assert report['candidate']['status']['version'] == '2.0.0'
    assert all(v['passed'] is False for v in report['candidate']['status']['verification'])


def test_NET_001_actual_ethernet_http_exchange(physical_bench):
    report = physical_bench.packets()
    physical_bench.record('NET-001', report)
    assert report['pcap_bytes'] > 0
    assert report['http_requests'] >= report['expected_file_requests']
    assert report['http_200_responses'] >= report['expected_file_requests']
    assert '/app.tar.gz' in report['analysis']
    assert all(count >= report['ota_count'] for count in report['successful_files'].values())
