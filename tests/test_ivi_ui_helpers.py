import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

HELPER = Path(__file__).parents[1] / 'dashboard/ivi/ui/ivi.js'
QML = Path(__file__).parents[1] / 'dashboard/ivi/ui/Ivi.qml'

PRELUDE = ("const fs=require('fs'),vm=require('vm'),assert=require('assert');"
           "vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));")


def run_node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    result = subprocess.run([node, '-e', PRELUDE + script, str(HELPER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_clock_and_sample_helpers():
    out = run_node(r"""
assert.strictEqual(clockText(new Date(2026, 0, 1, 11, 30)), '오전 11:30');
assert.strictEqual(clockText(new Date(2026, 0, 1, 0, 5)), '오전 12:05');
assert.strictEqual(clockText(new Date(2026, 0, 1, 15, 7)), '오후 3:07');
assert.strictEqual(clockText(new Date(2026, 0, 1, 12, 0)), '오후 12:00');
assert.strictEqual(sameSample({speed: 1, x: 2, updated: 5}, {speed: 1, x: 2, updated: 6}), true);   // timestamp is ignored
assert.strictEqual(sameSample({speed: 1, x: 2}, {speed: 2, x: 2}), false);
assert.strictEqual(sameSample({can: true, workers: 4, pid: 1}, {can: true, workers: 4, pid: 1}), true);
assert.strictEqual(sameSample({a: 1}, undefined), false);
console.log('[1]');
""")
    assert out == [1]


def test_player_helpers():
    out = run_node(r"""
assert.strictEqual(formatTrackTime(0), '0:00');
assert.strictEqual(formatTrackTime(65.9), '1:05');
assert.strictEqual(formatTrackTime(600), '10:00');
assert.strictEqual(formatTrackTime(-4), '0:00');
assert.strictEqual(formatTrackTime(undefined), '0:00');
assert.strictEqual(stepTrack(0, 5, 1), 1);
assert.strictEqual(stepTrack(4, 5, 1), 0);      // wraps around the playlist
assert.strictEqual(stepTrack(0, 5, -1), 4);
assert.strictEqual(stepTrack(2, 0, 1), 0);
assert.deepStrictEqual(playerTick(10, 214, true), {elapsed: 11, ended: false});
assert.deepStrictEqual(playerTick(10, 214, false), {elapsed: 10, ended: false});
assert.deepStrictEqual(playerTick(213, 214, true), {elapsed: 0, ended: true});
assert.strictEqual(progressFraction(107, 214), 0.5);
assert.strictEqual(progressFraction(5, 0), 0);
assert.strictEqual(progressFraction(500, 214), 1);
console.log('[1]');
""")
    assert out == [1]


def test_qml_text_sizes_come_from_tokens_and_are_legible():
    ui = QML.parent
    theme = (ui / 'Theme.qml').read_text()
    scale = [int(n) for n in re.findall(r'readonly property int t[A-Z]\w*:\s*(\d+)', theme)]
    assert scale and min(scale) >= 28
    for qml in ui.glob('*.qml'):
        for n in re.findall(r'pixelSize:\s*(\d+)', qml.read_text()):
            assert int(n) >= 28, (qml.name, n)


def test_every_qml_sibling_is_shipped_with_the_ui_dir(tmp_path):
    from dashboard.ivi.build import payload
    slot = payload(tmp_path / 'V2', 'V2')
    shipped = {p.name for p in (slot / 'ui').iterdir()}
    assert {'Ivi.qml', 'Theme.qml', 'qmldir', 'ivi.js', 'icons.js', 'geo.js', 'RoadMap.qml', 'assets'} <= shipped


def test_no_dead_files_ship_in_the_ui_dir():
    ui = QML.parent
    texts = {p.name: p.read_text() for p in ui.iterdir() if p.is_file() and p.suffix in ('.qml', '.js')}
    for path in ui.rglob('*'):
        if not path.is_file() or path.relative_to(ui).parts[0] == 'fonts' or path.name in ('Ivi.qml', 'qmldir'):
            continue
        needle = str(path.relative_to(ui)) if path.suffix == '.png' else path.stem
        assert any(needle in text for name, text in texts.items() if name != path.name), path.name


def test_strip_does_not_repeat_what_the_cluster_above_shows():
    # Ivi.qml is only the bottom strip of the cockpit; speed, rpm and gear belong to the digital cluster.
    text = re.sub(r'//[^\n]*', '', QML.read_text()).lower()   # code only, not comments
    for word in ('km/h', 'rpm', 'speedtext', 'nav.speed'):
        assert word not in text, word
    assert 'speedText' not in HELPER.read_text()


def test_radio_station_table_and_stepping():
    out = run_node(r"""
assert.strictEqual(RADIO_STATIONS.length, 5);
RADIO_STATIONS.forEach(function (s, i) {
  assert.strictEqual(s.name, 'Radio ' + (i + 1));          // no real station names
  assert.ok(s.freq >= RADIO_MIN && s.freq <= RADIO_MAX);
  assert.ok(s.signal >= 1 && s.signal <= 4);
  if (i) assert.ok(s.freq > RADIO_STATIONS[i - 1].freq);   // the dial order is the frequency order
});
assert.strictEqual(stepStation(0, 1), 1);
assert.strictEqual(stepStation(4, 1), 0);                  // wraps around
assert.strictEqual(stepStation(0, -1), 4);
assert.strictEqual(stepStation(7, 1), 1);                  // out-of-range index is treated as station 0, not trusted
assert.strictEqual(radioFreqText(93.1), '93.1');
assert.strictEqual(radioFreqText(100), '100.0');
assert.strictEqual(dialFraction(RADIO_MIN), 0);
assert.strictEqual(dialFraction(RADIO_MAX), 1);
assert.strictEqual(dialFraction(RADIO_MIN - 5), 0);
assert.strictEqual(dialFraction(RADIO_MAX + 5), 1);
assert.ok(Math.abs(dialFraction((RADIO_MIN + RADIO_MAX) / 2) - 0.5) < 1e-9);
var seen = RADIO_STATIONS.map(function (s) { return dialFraction(s.freq); });
assert.strictEqual(new Set(seen).size, 5);                 // each station puts the needle somewhere else
console.log('[1]');
""")
    assert out == [1]
