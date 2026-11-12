import json
import os
from pathlib import Path
import shutil
import subprocess


def test_local_preview_is_isolated_and_keeps_recovery_refusal_last(tmp_path, monkeypatch):
    import demo.local_cockpit_replay as preview
    monkeypatch.setattr(preview, 'COCKPIT', str(tmp_path / 'cockpit-data'))
    monkeypatch.setattr(preview, 'IVI', str(tmp_path / 'ivi-data'))
    monkeypatch.setattr(preview.time, 'sleep', lambda _: None)
    qml = preview.prepare()
    assert 'file:///run/' not in qml.read_text()
    assert '/tmp/claude-' not in qml.read_text()
    assert 'LOCAL PREVIEW' in qml.read_text()
    preview.replay()
    data = json.loads((tmp_path / 'cockpit-data/data.json').read_text())
    assert [row['stage'] for row in data['ota']['timeline']] == [
        'metadata', 'verify', 'recover', 'recovery_failed']
    assert data['ota']['stage'] == 'recovery_failed'
    assert data['ivi']['version'] == 'V3'  # a refused recovery must not claim V2 is running
    assert data['tap']['cluster']['speed'] >= 0
    assert data['ivi_ui'].startswith(tmp_path.as_uri())
    assert (tmp_path / 'ivi-data/status.json').exists()
    if shutil.which('qmlscene'):
        harness = tmp_path / 'Check.qml'
        harness.write_text('''import QtQuick 2.15
Item {
    property var cockpit
    Component.onCompleted: {
        var component = Qt.createComponent(%s)
        cockpit = component.createObject(null)
        if (!cockpit) { console.error(component.errorString()); Qt.exit(2) }
    }
    Timer { interval: 800; running: true; onTriggered: { cockpit.close(); Qt.quit() } }
}
''' % json.dumps(qml.as_uri()))
        done = subprocess.run(['qmlscene', str(harness)], capture_output=True, text=True, timeout=5,
                              env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                                   'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
        assert done.returncode == 0, done.stderr
        assert 'Error:' not in done.stderr, done.stderr
