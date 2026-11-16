"""The CI helpers are code too: a broken skip guard or release signer must fail loudly, not pass silently."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from capstone_ota.bundle import verify
from tools.ci import make_release, skip_guard, trace_matrix

ROOT = Path(__file__).parents[1]


def junit(path, cases):
    body = ''.join(f'<testcase classname="t" name="{n}">{inner}</testcase>' for n, inner in cases)
    path.write_text(f'<testsuites><testsuite>{body}</testsuite></testsuites>')
    return path


# --- skip guard -------------------------------------------------------------------------------

def test_skip_guard_rejects_a_forbidden_skip_reason(tmp_path, capsys):
    xml = junit(tmp_path / 'j.xml', [('a', ''), ('b', '<skipped message="build the actual Qt dashboard first"/>')])
    assert skip_guard.main([str(xml), '--forbid', 'build the']) == 1
    assert 'forbidden reason' in capsys.readouterr().err


def test_skip_guard_allows_unrelated_skips_and_counts_them(tmp_path, capsys):
    xml = junit(tmp_path / 'j.xml', [('a', '<skipped message="needs --hardware-config"/>')])
    assert skip_guard.main([str(xml), '--forbid', 'build the']) == 0
    assert 'skipped tests: 1' in capsys.readouterr().out


def test_skip_guard_enforces_a_skip_budget(tmp_path):
    xml = junit(tmp_path / 'j.xml', [(n, '<skipped message="x"/>') for n in 'abc'])
    assert skip_guard.main([str(xml), '--max-skipped', '2']) == 1
    assert skip_guard.main([str(xml), '--max-skipped', '3']) == 0


# --- traceability -----------------------------------------------------------------------------

def test_every_test_function_named_in_the_spec_exists():
    """If a hardware test is renamed, docs/TEST_CASES.md must follow."""
    report = trace_matrix.build(ROOT / 'docs/TEST_CASES.md', ROOT / 'tests', None)
    assert [r['id'] for r in report['rows'] if r['status'] == 'MISSING'] == []
    assert {r['id'] for r in report['rows']} >= {'CAN-001', 'SEC-003', 'NET-001', 'OTA-CAN-002-attack'}


def test_spec_row_naming_a_deleted_function_is_reported(tmp_path):
    spec = tmp_path / 'spec.md'
    spec.write_text('| TC | a | b | c |\n| --- | --- | --- | --- |\n| ABC-001 / req | in | out | `test_gone_forever` |\n')
    tests = tmp_path / 't'
    tests.mkdir()
    (tests / 'test_x.py').write_text('def test_other(): pass\n')
    report = trace_matrix.build(spec, tests, None)
    assert report['rows'][0]['status'] == 'MISSING'
    assert trace_matrix.main(['--spec', str(spec), '--tests', str(tests), '--check']) == 1


def test_junit_results_flow_into_the_matrix(tmp_path):
    spec = tmp_path / 'spec.md'
    spec.write_text('| ABC-001 / one | i | o | `test_a` |\n| ABC-002 / two | i | o | `test_b` |\n'
                    '| ABC-003 / three | i | o | `test_c[param]` |\n')
    tests = tmp_path / 't'
    tests.mkdir()
    (tests / 'test_x.py').write_text('def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n')
    xml = junit(tmp_path / 'j.xml', [('test_a', ''), ('test_b', '<failure message="boom"/>'),
                                     ('test_c[param]', '<skipped message="s"/>')])
    status = {r['id']: r['status'] for r in trace_matrix.build(spec, tests, xml)['rows']}
    assert status == {'ABC-001': 'pass', 'ABC-002': 'fail', 'ABC-003': 'skip'}


# --- release builder --------------------------------------------------------------------------

@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / 'k.pem'
    path.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return path


def test_release_is_signed_hashed_and_verifiable(tmp_path, key_file):
    info = make_release.build('3.0.0', tmp_path / 'out', key_file, ROOT / 'release/versions.json')
    release = tmp_path / 'out/release'
    metadata = verify((release / 'release.json').read_bytes(), (release / 'release.sig').read_bytes(),
                      release / 'release-public.pem')
    assert (metadata['version'], metadata['svn']) == ('3.0.0', 2)
    assert metadata['sha256'] == info['files']['app.tar.gz']
    assert info['verified_with_own_public_key'] is True and info['label'] == 'V3'
    sums = (release / 'SHA256SUMS').read_text().splitlines()
    assert sorted(line.split()[1] for line in sums) == ['app.tar.gz', 'release.json', 'release.sig']


def test_tampered_release_fails_verification(tmp_path, key_file):
    make_release.build('2.0.0', tmp_path / 'out', key_file, ROOT / 'release/versions.json')
    release = tmp_path / 'out/release'
    raw = (release / 'release.json').read_bytes().replace(b'"2.0.0"', b'"9.9.9"')
    with pytest.raises(ValueError, match='signature'):
        verify(raw, (release / 'release.sig').read_bytes(), release / 'release-public.pem')


def test_unknown_version_is_refused_before_anything_is_built(tmp_path, key_file):
    with pytest.raises(ValueError, match='not in'):
        make_release.build('7.7.7', tmp_path / 'out', key_file, ROOT / 'release/versions.json')
    assert not (tmp_path / 'out').exists()


def test_missing_signing_key_is_a_clear_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('OTA_SIGNING_KEY', raising=False)
    assert make_release.main(['--version', '3.0.0', '--output', str(tmp_path / 'o')]) == 2
    assert 'OTA_SIGNING_KEY' in capsys.readouterr().err


def test_signing_key_from_environment_leaves_no_temp_key_behind(tmp_path, key_file, monkeypatch):
    monkeypatch.setenv('OTA_SIGNING_KEY', key_file.read_text())
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'tmp'))
    (tmp_path / 'tmp').mkdir()
    import tempfile
    tempfile.tempdir = None
    try:
        assert make_release.main(['--version', '2.0.0', '--output', str(tmp_path / 'o')]) == 0
    finally:
        tempfile.tempdir = None
    assert list((tmp_path / 'tmp').glob('*.pem')) == []
    assert (tmp_path / 'o/release/release.sig').is_file()
