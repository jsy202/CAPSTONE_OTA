"""Progress is observational: real outcomes and safety decisions stay authoritative."""
import json
from pathlib import Path

import pytest

from capstone_ota.device import Device, fetch
from test_device import releases, serving
from test_bundle import keys, payload


def read(root):
    return json.loads((root / 'progress.json').read_text())


def test_real_http_progress_and_failed_candidate_restore_previous_version(releases, keys, tmp_path):
    root = tmp_path / 'device'
    device = Device(root)
    with serving(releases) as url:
        device.update(url + '/v1', public_key=keys[1])
        first = read(root)
        assert first['stage'] == 'committed'
        assert first['version'] == '1.0.0'
        assert first['received'] == first['total'] > 0
        device.update(url + '/bad')
    final = read(root)
    assert final['stage'] == 'rolled_back'
    assert final['version'] == '1.0.0'
    assert final['candidate'] == '1.3.0'


def test_download_reports_measured_bytes_and_content_length(releases, tmp_path):
    observations = []
    with serving(releases) as url:
        fetch(url + '/v1/app.tar.gz', tmp_path / 'archive', 1000000,
              progress=lambda received, total: observations.append((received, total)))
    size = (tmp_path / 'archive').stat().st_size
    assert observations[-1] == (size, size)
    assert all(0 <= received <= size for received, _ in observations)


def test_download_deadline_cannot_be_swallowed_by_progress_write(releases, keys, tmp_path, monkeypatch):
    import signal
    import capstone_ota.device as module
    original = module.atomic_write
    interrupted = []
    def write(path, content):
        if path.name == 'progress.json' and json.loads(content)['stage'] == 'download' and not interrupted:
            interrupted.append(True)
            signal.raise_signal(signal.SIGALRM)
        return original(path, content)
    monkeypatch.setattr(module, 'atomic_write', write)
    with serving(releases) as url:
        with pytest.raises(TimeoutError, match='download deadline exceeded'):
            Device(tmp_path / 'device').update(url + '/v1', public_key=keys[1])
    assert interrupted


def test_telemetry_failure_does_not_fail_real_update(releases, keys, tmp_path):
    root = tmp_path / 'device'; root.mkdir()
    (root / 'progress.json').mkdir()  # Cannot replace this directory with a status file.
    with serving(releases) as url:
        assert Device(root).update(url + '/v1', public_key=keys[1])['result'] == 'committed'


def test_new_terminal_display_does_not_add_a_verdict_affecting_event_write(releases, keys, tmp_path, monkeypatch):
    device = Device(tmp_path / 'device')
    original = device._event
    def event(name):
        if name == 'rolled_back':
            raise OSError('terminal log unavailable')
        original(name)
    monkeypatch.setattr(device, '_event', event)
    with serving(releases) as url:
        device.update(url + '/v1', public_key=keys[1])
        assert device.update(url + '/bad')['result'] == 'rolled_back'


def test_recovery_refusal_is_not_reported_as_success(releases, keys, tmp_path):
    root = tmp_path / 'device'
    device = Device(root)
    with serving(releases) as url:
        device.update(url + '/v1', public_key=keys[1])
    state = json.loads((root / 'state.json').read_text())
    state.update(phase='trial', result='trial', pending=dict(state['committed'], slot='B'))
    device._save(state)
    (root / 'slots/A/extra').write_text('substituted')
    with pytest.raises(ValueError, match='recovery failed'):
        device.update()
    assert read(root)['stage'] == 'recovery_failed'
    assert 'hash mismatch' in read(root)['detail']


def test_slot_projection_retains_trial_history_but_marks_only_selected_slot(releases, keys, tmp_path):
    root = tmp_path / 'device'
    with serving(releases) as url:
        Device(root).update(url + '/v1', public_key=keys[1])
        before = read(root)['slot_view']
        assert before['selected'] == 'A'
        assert before['A']['version'] == '1.0.0'
        assert before['B']['version'] is None
        Device(root).update(url + '/bad')
    view = read(root)['slot_view']
    assert view['selected'] == 'A'
    assert view['A'] == {'version': '1.0.0', 'role': 'committed'}
    assert view['B'] == {'version': '1.3.0', 'role': 'retained'}


def test_recovery_refusal_keeps_recorded_version_separate_from_execution(releases, keys, tmp_path):
    root = tmp_path / 'device'; d = Device(root)
    with serving(releases) as url:
        d.update(url + '/v1', public_key=keys[1])
    state = d._load();state.update(phase='trial', pending=dict(state['committed'], slot='B'))
    d._save(state)
    (root / 'slots/A/extra').write_text('tampered')
    with pytest.raises(ValueError, match='recovery failed'):
        d.update()
    view = read(root)['slot_view']
    assert view['A']['version'] == '1.0.0'
    assert view['A']['role'] == 'recovery_failed'
