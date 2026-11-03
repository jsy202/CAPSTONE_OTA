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


@pytest.mark.parametrize('stage,selected,verified,role', [
    ('rolled_back', 'A', 'A', 'committed'),
    ('recovery_failed', 'B', None, 'recovery_failed'),
])
def test_large_comparison_and_slots_do_not_confuse_history_with_healthy_execution(tmp_path, stage, selected, verified, role):
    root = tmp_path / 'device'; root.mkdir()
    data = {'stage': stage, 'updated': time.time(), 'pid': os.getpid(), 'version': '2.0.0', 'candidate': '3.0.0',
            'mismatch': {'expected': 62, 'qml': 45, 'passed': False}, 'mismatch_slot': 'B', 'mismatch_version': '3.0.0',
            'observation': {'expected': 65, 'qml': 65, 'passed': True}, 'verified_slot': verified,
            'slot_view': {'available': True, 'selected': selected, 'A': {'version': '2.0.0', 'role': role},
                          'B': {'version': '3.0.0', 'role': 'retained'}}}
    (root / 'progress.json').write_text(json.dumps(data))
    snapshot = tmp_path / 'panel.json'
    result = subprocess.run([str(ROOT / 'dashboard/qt_progress/run'), '--root', str(root), '--snapshot', str(snapshot), '--quit-after', '350'],
                            env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    view = json.loads(snapshot.read_text())
    assert view['expected'] == '62' and view['actual'] == '45'
    assert view['historical_mismatch'] is True
    assert view['slot_a_version'] == 'V2' and view['slot_b_version'] == 'V3'
    assert view['slot_a_selected'] is (selected == 'A')
    assert view['slot_b_selected'] is (selected == 'B')
    assert view['slot_a_verified'] is (stage == 'rolled_back')
    assert view['slot_b_verified'] is False
    assert ('B → A' in view['route']) is (stage == 'rolled_back')


@pytest.mark.parametrize('age,stage,alive,unconfirmed,title', [
    (0, 'verify', True, False, '실제 계기판 검증'),
    (15, 'rolled_back', True, True, '원격 연결 끊김 · 마지막 기록'),
])
def test_remote_writer_identity_and_observer_freshness_are_distinct(tmp_path, age, stage, alive, unconfirmed, title):
    root=tmp_path/'device';root.mkdir()
    (root/'progress.json').write_text(json.dumps({'stage':stage,'pid':0,'updated':1,'version':'2.0.0',
        '_remote':{'received':time.time()-age,'writer_alive':alive,'source':'pi2'}}))
    snapshot=tmp_path/'panel.json'
    run=subprocess.run([str(ROOT/'dashboard/qt_progress/run'),'--root',str(root),'--snapshot',str(snapshot),'--quit-after','300'],env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=5)
    assert run.returncode==0,run.stderr
    data=json.loads(snapshot.read_text())
    assert data['unconfirmed'] is unconfirmed
    assert data['title']==title
    if unconfirmed:assert data['success'] is False


def test_equal_values_with_expired_input_are_not_labeled_as_a_speed_difference(tmp_path):
    root=tmp_path/'device';root.mkdir()
    (root/'progress.json').write_text(json.dumps({'stage':'verification_failed','updated':time.time(),'pid':os.getpid(),
        'mismatch':{'expected':62,'qml':62,'valid':False,'passed':False}}))
    snap=tmp_path/'panel.json'
    p=subprocess.run([str(ROOT/'dashboard/qt_progress/run'),'--root',str(root),'--snapshot',str(snap),'--quit-after','300'],env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=5)
    assert p.returncode==0,p.stderr
    data=json.loads(snap.read_text())
    assert data['expected']==data['actual']=='62'
    assert '유효기간' in data['comparison_note'] and '차이 0' not in data['comparison_note']
