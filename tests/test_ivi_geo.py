"""Route maths for the IVI map view (dashboard/ivi/ui/geo.js), exercised with node."""
from pathlib import Path
import shutil
import subprocess

import pytest

HELPER = Path(__file__).parents[1] / 'dashboard/ivi/ui/geo.js'

SCRIPT = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
// geo.js is a QML ".pragma library" file: drop the pragma line, then run it as plain JS.
const src = fs.readFileSync(process.argv[1], 'utf8').split('\n').filter(l => !l.startsWith('.pragma')).join('\n');
vm.runInThisContext(src);
const near = (a, b, eps = 1e-6) => assert(Math.abs(a - b) < eps, a + ' != ' + b);

// ---- formatting ----
assert.strictEqual(formatDistance(4200), '4.2km');
assert.strictEqual(formatDistance(4249), '4.2km');
assert.strictEqual(formatDistance(999.4), '1.0km');   // never "1000m"
assert.strictEqual(formatDistance(1000), '1.0km');
assert.strictEqual(formatDistance(640), '640m');
assert.strictEqual(formatDistance(87), '90m');
assert.strictEqual(formatDistance(3), '0m');
assert.strictEqual(formatDistance(-5), '0m');
assert.strictEqual(formatDistance(NaN), '--');

// ---- straight route: no turn, lengths ----
const straight = buildRoute([0, 0, 0, 100, 0, 200, 0, 300]);
near(straight.length, 300);
assert.strictEqual(straight.turns.length, 0);
assert.strictEqual(routeInfo(straight, {x: 0, y: 50, distance: 50}).turn, null);

// ---- L route, right turn at 100 m (north, then east) ----
const right = buildRoute([0, 0, 0, 50, 0, 100, 50, 100, 100, 100]);
assert.strictEqual(right.turns.length, 1);
near(right.turns[0].d, 100);
near(right.turns[0].angle, 90, 1);
let info = routeInfo(right, {x: 0, y: 20, distance: 20});
near(info.remaining, 180);                   // length 200 minus 20 m travelled
near(info.turn.distance, 80);
assert(info.turn.angle > 0);                 // right = positive
// past the only turn of an open route the search wraps around to it
info = routeInfo(right, {x: 50, y: 100, distance: 150});
near(info.turn.distance, 150);

// ---- left turn is negative ----
const left = buildRoute([0, 0, 0, 100, -100, 100]);
assert(left.turns[0].angle < -80 && left.turns[0].angle > -100);

// ---- gentle curve below the threshold is not a turn; a 30 degree kink is ----
const curve = [];
for (let a = 0; a <= 40; a += 4) curve.push(200 * Math.sin(a * Math.PI / 180), 200 * Math.cos(a * Math.PI / 180));
assert.strictEqual(buildRoute(curve).turns.length, 0);   // 40 degrees spread over a long arc
const kink = buildRoute([0, 0, 0, 100, 0, 200, 60, 305, 120, 410]);   // 30 degree change at 200 m... plus nothing else
assert.strictEqual(kink.turns.length, 1);
near(kink.turns[0].d, 200, 1);

// a sharp bend sampled by several vertices still yields ONE turn with the whole angle
const round = buildRoute([0, 0, 0, 100, 5, 110, 20, 118, 40, 120, 140, 120]);
assert.strictEqual(round.turns.length, 1);
assert(round.turns[0].angle > 60 && round.turns[0].angle < 110);

// ---- loop: next turn after the last one wraps to the first one ----
const loop = buildRoute([0, 0, 0, 100, 100, 100, 100, 0, 0, 0]);
assert.strictEqual(loop.turns.length, 4);        // three corners plus the closing vertex at 0 m
info = routeInfo(loop, {x: 0, y: 0, distance: 390});
near(loop.length, 400);
near(info.remaining, 10);
near(info.turn.distance, 10, 1e-6);         // the closing vertex is itself a corner
// distance counter keeps growing beyond the length: it wraps
info = routeInfo(loop, {x: 0, y: 50, distance: 450});
near(info.along, 50);
near(info.remaining, 350);

// ---- without nav.distance the position is projected onto the route ----
info = routeInfo(loop, {x: 3, y: 60});
near(info.along, 60, 1e-6);
info = routeInfo(loop, {x: 97, y: 100 + 4});   // slightly outside the top right corner
assert(info.along > 190 && info.along < 215);

// ---- pointAt / indexAt ----
const pt = pointAt(loop, 150);
near(pt[0], 50); near(pt[1], 100);
assert.strictEqual(indexAt(loop.cum, 0), 0);
assert.strictEqual(indexAt(loop.cum, 399.9), 3);
assert.strictEqual(indexAt(loop.cum, 1e9), 3);

// ---- degenerate input never throws ----
assert.strictEqual(buildRoute([]), null);
assert.strictEqual(buildRoute([1, 1]), null);
assert.strictEqual(routeInfo(null, {x: 0, y: 0}), null);

// ---- street names: only real names, nearest segment within range ----
const roads = [{c: 'primary', p: [0, 0, 100, 0], n: 'Main St'}, {c: 'primary', p: [0, 50, 100, 50]},
               {c: 'residential', p: [0, 20, 100, 20], name: 'Side Rd'}];
const named = namedRoads(roads);
assert.strictEqual(named.length, 2);
assert.strictEqual(nearestName(named, 50, 3, 30), 'Main St');
assert.strictEqual(nearestName(named, 50, 15, 30), 'Side Rd');
assert.strictEqual(nearestName(named, 50, 400, 30), '');
assert.strictEqual(nearestName([], 0, 0, 30), '');
console.log('ok');
'''


def test_route_helpers_follow_the_documented_rules():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    result = subprocess.run([node, '-e', SCRIPT, str(HELPER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'
