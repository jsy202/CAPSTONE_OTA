"""Real signed IVI updates and rollback-slot substitution; no GUI or CPU stress required."""
from demo.ivi_attack import run


def test_ivi_security_fixture_blocks_authentic_old_release_without_a_cluster(tmp_path):
    output = tmp_path / 'ivi-security'
    result = run(output)
    assert result['compatible']['result'] == 'committed'
    assert result['compatible']['target'] == 'ivi'
    assert result['compatible']['version'] == '3.0.0'
    assert result['attack']['target'] == 'ivi'
    assert result['attack']['result'] == 'recovery_failed'
    assert result['attack']['min_svn'] == 2
    assert result['attack']['verification'][0]['error'] == 'ATTACK_FIXTURE_ROLLBACK_REQUEST'
    assert result['attack_evidence']['old_signature_valid'] is True
    assert result['attack_evidence']['trusted_journal_unchanged'] is True
    assert result['attack_evidence']['old_app_executed'] is False
    assert not list((output / 'payloads').rglob('dashboard'))
