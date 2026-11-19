import os
from pathlib import Path
import shutil
import subprocess

import pytest

UI = Path(__file__).parents[1] / 'dashboard/ivi/ui'


def test_player_js_pure_logic():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
// QML-only ".pragma library" line is not valid JS; drop it for node.
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8').replace(/^\.pragma.*$/m, ''));
assert.strictEqual(formatTime(0), '0:00');
assert.strictEqual(formatTime(65.9), '1:05');
assert.strictEqual(formatTime(600), '10:00');
assert.strictEqual(formatTime(-5), '0:00');
assert.strictEqual(formatTime(NaN), '0:00');
assert.strictEqual(wrapIndex(4, 4), 0);
assert.strictEqual(wrapIndex(-1, 4), 3);
assert.strictEqual(wrapIndex(2, 4), 2);
assert.strictEqual(wrapIndex(1, 0), 0);
assert.strictEqual(clamp01(1.5), 1);
assert.strictEqual(clamp01(-1), 0);
assert.strictEqual(ratioFromX(50, 0, 200), 0.25);
assert.strictEqual(ratioFromX(500, 0, 200), 1);
assert.strictEqual(ratioFromX(5, 0, 0), 0);
// advance within a track
let s = advance({index: 0, position: 10}, 5, [100, 100]);
assert.deepStrictEqual(s, {index: 0, position: 15});
// track end rolls to next and carries nothing over
s = advance({index: 0, position: 98}, 5, [100, 100]);
assert.deepStrictEqual(s, {index: 1, position: 0});
// last track wraps to first
s = advance({index: 1, position: 99.5}, 1, [100, 100]);
assert.deepStrictEqual(s, {index: 0, position: 0});
// empty playlist is inert
assert.deepStrictEqual(advance({index: 0, position: 3}, 1, []), {index: 0, position: 0});
assert.strictEqual(upNext(1, 5, 3).join(), '2,3,4');
assert.strictEqual(upNext(3, 5, 3).join(), '4,0,1');
assert.strictEqual(upNext(0, 2, 3).join(), '1');
assert.strictEqual(upNext(0, 1, 3).join(), '');
'''
    r = subprocess.run([node, '-e', script, str(UI / 'player.js')], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_music_player_qml_loads_without_errors(tmp_path):
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed to load the component')
    harness = tmp_path / 'Harness.qml'
    harness.write_text('''import QtQuick 2.15
import "%s"
Item {
    width: 1920; height: 600
    MusicPlayer { id: p; width: 1450; height: 540 }
    Timer { interval: 400; running: true; onTriggered: {
        p.next(); p.togglePlaying(); p.previous(); Qt.quit() } }
}
''' % UI.as_uri())
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    r = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True,
                       env=env, timeout=60)
    assert 'Error:' not in r.stderr, r.stderr
    assert 'is not a type' not in r.stderr, r.stderr
