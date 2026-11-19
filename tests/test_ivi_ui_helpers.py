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


def test_formatting_speed_clock_and_lap_helpers():
    out = run_node(r'''
assert.strictEqual(formatDistance(0), '0 m');
assert.strictEqual(formatDistance(842.4), '842 m');
assert.strictEqual(formatDistance(5600), '5.6 km');
assert.strictEqual(formatDistance(-3), '0 m');
assert.strictEqual(formatDistance(undefined), '0 m');
assert.strictEqual(routeLength([0, 0, 3, 4, 3, 10]), 11);
assert.strictEqual(routeLength([]), 0);
assert.strictEqual(routeLength(null), 0);
const p = lapProgress(2750, 1000);
assert.strictEqual(p.lap, 3); assert.strictEqual(p.fraction, 0.75); assert.strictEqual(p.into, 750);
assert.deepStrictEqual(lapProgress(10, 0), {lap: 0, fraction: 0, into: 0});
assert.strictEqual(speedText({speed: 59.6}, true), '60');
assert.strictEqual(speedText({speed: 0}, true), '0');
assert.strictEqual(speedText({speed: 12}, false), '–');   // no live CAN: never show a number
assert.strictEqual(speedText({}, true), '–');             // nothing read yet
assert.strictEqual(speedText(null, true), '–');
assert.strictEqual(clockText(new Date(2026, 0, 1, 11, 30)), '오전 11:30');
assert.strictEqual(clockText(new Date(2026, 0, 1, 0, 5)), '오전 12:05');
assert.strictEqual(clockText(new Date(2026, 0, 1, 15, 7)), '오후 3:07');
assert.strictEqual(clockText(new Date(2026, 0, 1, 12, 0)), '오후 12:00');
assert(mapScale(0) > mapScale(60) && mapScale(60) > mapScale(120));
assert.strictEqual(mapScale(undefined), mapScale(0));
assert.strictEqual(mapScale(500), mapScale(120));
console.log('[1]');
''')
    assert out == [1]


def test_vertex_lookup_and_next_turn_geometry():
    out = run_node(r'''
// 100 m north, then 100 m east (a right turn), then 100 m north (a left turn): an S shape.
const route = [0, 0, 0, 100, 100, 100, 100, 200];
const cum = cumulative(route);
assert.deepStrictEqual(cum, [0, 100, 200, 300]);
assert.strictEqual(vertexAt(cum, 0), 0);
assert.strictEqual(vertexAt(cum, 99.9), 0);
assert.strictEqual(vertexAt(cum, 100), 1);
assert.strictEqual(vertexAt(cum, 250), 2);
assert.strictEqual(vertexAt(cum, 9999), 2);
let t = nextTurn(route, 40);
assert(Math.abs(t.distance - 60) < 1e-6, t.distance);
assert.strictEqual(t.direction, 'right'); assert(Math.abs(t.angle - 90) < 1e-6);
t = nextTurn(route, 150);                      // past the first turn, so the left one is next
assert(Math.abs(t.distance - 50) < 1e-6); assert.strictEqual(t.direction, 'left');
assert(Math.abs(t.angle + 90) < 1e-6);
assert.strictEqual(nextTurn([0, 0, 0, 100, 0, 200], 10), null);   // straight road: no turn
assert.strictEqual(nextTurn([0, 0], 0), null);
assert.strictEqual(nextTurn(null, 0), null);
// a gentle curve of 10 degree steps every 10 m counts as one turn once 25 degrees have built up
const curve = [0, 0]; let x = 0, y = 0, b = 0;
for (let i = 0; i < 12; i++) { x += 10 * Math.sin(b * Math.PI / 180); y += 10 * Math.cos(b * Math.PI / 180); curve.push(x, y); b -= 10; }
const c = nextTurn(curve, 0);
assert(c && c.direction === 'left' && c.angle <= -25, JSON.stringify(c));
// the route is a loop: the turn after the last vertex wraps to the start
const loop = [0, 0, 0, 100, 100, 100, 100, 0, 0, 0];
const w = nextTurn(loop, 390);
assert(w && w.distance < 20, JSON.stringify(w));
// remaining distance along the loop
assert.strictEqual(remainingDistance(1000, 250), 750);
assert.strictEqual(remainingDistance(1000, 1250), 750);
assert.strictEqual(remainingDistance(0, 5), 0);
console.log('[1]');
''')
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
    assert scale and min(scale) >= 28 and max(scale) >= 200
    for qml in ui.glob('*.qml'):
        for n in re.findall(r'pixelSize:\s*(\d+)', qml.read_text()):
            assert int(n) >= 28, (qml.name, n)


def test_every_qml_sibling_is_shipped_with_the_ui_dir(tmp_path):
    from dashboard.ivi.build import payload
    slot = payload(tmp_path / 'V2', 'V2')
    shipped = {p.name for p in (slot / 'ui').iterdir()}
    assert {'Ivi.qml', 'Theme.qml', 'qmldir', 'ivi.js', 'icons.js', 'car.png'} <= shipped
