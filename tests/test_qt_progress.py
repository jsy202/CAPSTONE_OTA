"""Native Qt read-only monitor; no update process is owned by the viewer."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

ROOT = Path(__file__).parents[1]
BINARY = ROOT / '.demo/progress-build/capstone-ota-progress'
pytestmark = pytest.mark.skipif(not BINARY.exists(), reason='build the native progress panel first')


@pytest.mark.parametrize('stage,title,extra', [
    ('download', '앱 다운로드', {'received': 72, 'total': 100}),
    ('rolled_back', 'V2 복구 완료', {'version': '2.0.0'}),
    ('recovery_failed', '복구 차단 · 서비스 미복원', {'version': '2.0.0'}),
    (None, '진행 정보 대기', {}),
])
def test_real_qml_panel_reports_download_and_distinguishes_refusal(tmp_path, stage, title, extra):
    assert BINARY.is_file(), 'build the Qt progress panel before running its native tests'
    import time
    root = tmp_path / 'device'; root.mkdir()
    if stage:
        (root / 'progress.json').write_text(json.dumps(dict(stage=stage, updated=time.time(),
                                                           pid=os.getpid(), **extra)))
    snapshot = tmp_path / 'panel.json'
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    runtime = ROOT / '.demo/qt-build/runtime.json'
    if runtime.exists():
        prefix = json.loads(runtime.read_text())['qt_prefix']
        if prefix:
            environment['CAPSTONE_QT_PREFIX'] = prefix
    run = subprocess.run([str(ROOT / 'dashboard/qt_progress/run'), '--root', str(root),
                          '--snapshot', str(snapshot), '--quit-after', '500'],
                         env=environment, capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stderr
    observed = json.loads(snapshot.read_text())
    assert observed['title'] == title
    if stage == 'download':
        assert observed['percent'] == '72%'
    if stage == 'recovery_failed':
        assert observed['success'] is False


@pytest.mark.parametrize('contents,title,unconfirmed,success', [
    ('{partial', '진행 정보 대기', False, False),
    ({'stage': 'unexpected'}, '진행 정보 대기', False, False),
    ({'stage': 'download', 'updated': 1}, '진행 중단 · 결과 미확인', True, False),
    ({'stage': 'verify', 'pid': 0}, '진행 중단 · 결과 미확인', True, False),
    ({'stage': 'verify', 'updated': 4102444800}, '진행 중단 · 결과 미확인', True, False),
    ({'stage': 'rolled_back', 'version': '2.0.0', 'pid': 0, 'updated': 1}, 'V2 복구 완료', False, True),
])
def test_panel_handles_interrupted_writers_without_inventing_success(tmp_path, contents, title, unconfirmed, success):
    root = tmp_path / 'device'
    root.mkdir()
    if isinstance(contents, dict):
        contents = json.dumps({'updated': time.time(), 'pid': os.getpid(), **contents})
    (root / 'progress.json').write_text(contents)
    snapshot = tmp_path / 'panel.json'
    run = subprocess.run([str(ROOT / 'dashboard/qt_progress/run'), '--root', str(root),
                          '--snapshot', str(snapshot), '--quit-after', '300'],
                         env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software'),
                         capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stderr
    observed = json.loads(snapshot.read_text())
    assert observed['title'] == title
    assert observed['unconfirmed'] is unconfirmed
    assert observed['success'] is success
