import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_fast_update_and_rejected_recovery_keep_order_without_false_success():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.strictEqual(versionText({stage: 'rolled_back', version: '2.0.0', candidate: '3.0.0'}), '2.0.0');
assert.strictEqual(versionText({stage: 'verify', version: '2.0.0', candidate: '3.0.0'}), '2.0.0 → 3.0.0');
assert.strictEqual(versionText({stage: 'recover', version: '2.0.0', candidate: '3.0.0'}), '3.0.0 → 2.0.0');
assert.strictEqual(versionText({stage: 'recovery_failed', version: '2.0.0', candidate: '3.0.0'}), '2.0.0');
const names = {'2.0.0': 'V2', '3.0.0': 'V3'};
assert.strictEqual(versionText({stage: 'verify', version: '2.0.0', candidate: '3.0.0'}, names), 'V2 → V3');
assert.strictEqual(versionText({stage: 'recover', version: '2.0.0', candidate: '3.0.0'}, names), 'V3 → V2');
assert.deepStrictEqual(phase({stage: 'download', received: 10, total: 40}), {label: '다운로드', fraction: 0.25, kind: 'update', percent: '25%'});
assert.strictEqual(phase({stage: 'install', work: {kind: 'install', done: 3, total: 4}}).fraction, 0.75);
assert.strictEqual(phase({stage: 'recover', work: {kind: 'rollback', done: 1, total: 4}}).kind, 'rollback');
assert.strictEqual(phase({stage: 'recovery_failed', timeline: [{stage: 'recover'}]}).kind, 'blocked');
assert.strictEqual(phase({stage: 'recovery_failed', timeline: [{stage: 'recover'}, {stage: 'recovery_verified'}]}).kind, 'failed');
assert.strictEqual(phase({stage: 'install'}, true).fraction, 1);
assert.strictEqual(versionChange({stage: 'install', version: '2.0.0', candidate: '3.0.0'}, names), 'V2 → V3');
assert.strictEqual(versionChange({stage: 'recover', version: '2.0.0', candidate: '3.0.0'}, names), 'V3 → V2');
const labelled = consume({run: null, seen: 0, kinds: {}}, {timeline_id: 'n', timeline: [
    {stage: 'metadata', version: '2.0.0', candidate: '3.0.0'}, {stage: 'verify', version: '2.0.0', candidate: '3.0.0'}]}, names);
assert.deepStrictEqual(labelled.map(e => e.transition), ['V2 → V3', 'V2 → V3']);
const state = {run: null, seen: 0, kinds: {}};
assert.deepStrictEqual(consume(state, {stage: 'committed', version: '2.0.0'}), []);
const stages = ['metadata', 'signature_verified', 'download', 'install', 'trial',
                'verify', 'verification_failed', 'recover', 'recovery_failed'];
const ota = {timeline_id: 'one', timeline: stages.map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))};
const events = consume(state, ota);
assert.deepStrictEqual(events.map(e => e.kind), ['preparing', 'installed', 'rollback', 'rejected']);
assert(events[1].transition.includes('2.0.0') && events[1].transition.includes('3.0.0'));
assert.deepStrictEqual(consume(state, ota), []);
ota.timeline.push({stage: 'committed', version: '3.0.0'});
assert.deepStrictEqual(consume(state, ota), []); // a terminal rejection cannot become success
const normal = consume(state, {timeline_id: 'two', timeline: stages.slice(0, 6).concat(
    ['committed']).map(stage => ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(normal.map(e => e.kind), ['preparing', 'installed', 'accepted']);
const rollback = consume(state, {timeline_id: 'three', timeline: stages.slice(0, -1).concat(
    ['recovery_verified', 'recheck', 'rolled_back']).map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(rollback.map(e => e.kind), ['preparing', 'installed', 'rollback', 'restored']);
const failure = consume(state, {timeline_id: 'four', timeline: ['recover', 'recovery_verified',
    'recovery_failed'].map(stage => ({stage}))});
assert.deepStrictEqual(failure.map(e => e.kind), ['rollback', 'recovery_error']);
console.log(JSON.stringify(events));
'''
    result = subprocess.run([node, '-e', script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 4


def test_real_qml_animations_show_all_four_bands_in_order(tmp_path):
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed for the actual animation queue check')
    cockpit = (Path(__file__).parents[1] / 'dashboard/cockpit/Cockpit.qml').as_uri()
    harness = tmp_path / 'QueueCheck.qml'
    harness.write_text('''import QtQuick 2.15
Item {
    property var cockpit
    property var band
    property var observed: []
    Component.onCompleted: {
        var component = Qt.createComponent(%s)
        cockpit = component.createObject(null, {dataUrl: "file:///nonexistent-cockpit-test-data"})
        if (!cockpit) throw new Error(component.errorString())
        cockpit.d = {ota: {stage: "committed", version: "2.0.0"}}
        band = find(cockpit.contentItem)
        if (!band) throw new Error("missing band")
        var stages = ["metadata", "trial", "verify", "verification_failed", "recover", "recovery_failed"]
        cockpit.d = {ota: {timeline_id: "animation-test", timeline: stages.map(function(s) {
            return {stage: s, version: "2.0.0", candidate: "3.0.0"}
        })}}
    }
    function find(item) {
        if (item.objectName === "otaEventBand") return item
        for (var i = 0; item.children && i < item.children.length; i++) {
            var found = find(item.children[i]); if (found) return found
        }
        return null
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            if (observed[observed.length - 1] !== band.kind) observed.push(band.kind)
            if (observed.length === 4) {
                console.log("BAND_ORDER:" + JSON.stringify(observed))
                cockpit.close(); Qt.quit()
            }
        }
    }
}
''' % json.dumps(cockpit))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=20,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                               'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
    assert done.returncode == 0, done.stderr
    assert 'BAND_ORDER:["preparing","installed","rollback","rejected"]' in done.stderr, done.stderr
    assert 'Error:' not in done.stderr, done.stderr


def test_bar_shows_only_download_install_and_rollback_with_real_progress():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.deepStrictEqual(phase(null), {label: '', fraction: 0, kind: 'idle', percent: ''});
const half = phase({stage: 'download', received: 100, total: 200});
assert.strictEqual(half.label, '다운로드'); assert.strictEqual(half.percent, '50%'); assert.strictEqual(half.kind, 'update');
assert.strictEqual(phase({stage: 'download'}).percent, '');            // size unknown: no invented percentage
assert.strictEqual(phase({stage: 'install', work: {kind: 'install', done: 3, total: 4}}).percent, '75%');
assert.strictEqual(phase({stage: 'install'}).percent, '0%');           // no report yet: not a made-up value
const back = phase({stage: 'recover', work: {kind: 'rollback', done: 1, total: 4}});
assert.strictEqual(back.label, '롤백'); assert.strictEqual(back.kind, 'rollback'); assert.strictEqual(back.percent, '25%');
assert.strictEqual(phase({stage: 'rolled_back'}).kind, 'done');
assert.strictEqual(phase({stage: 'committed'}).label, '설치');
for (const quiet of ['signature', 'signature_verified', 'trial', 'verify', 'verification_failed', 'something_new'])
    assert.deepStrictEqual(phase({stage: quiet}), {label: '', fraction: 0, kind: 'idle', percent: ''});   // no other titles
assert.strictEqual(phase({stage: 'install'}, true).fraction, 1);       // a replayed (finished) stage shows a full bar
'''
    subprocess.run([node, '-e', script, str(helper)], check=True)


def test_gauge_says_what_a_refused_recovery_is_instead_of_a_bare_error():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
// A recovery refused before the image checked out = the defence blocking a tampered image (green "blocked"); after = a real failure.
const refused = phase({stage: 'error', detail: 'recovery failed; no successful recovery is claimed', timeline: [{stage: 'recover'}]});
assert.strictEqual(refused.label, '롤백'); assert.strictEqual(refused.kind, 'blocked');
assert.strictEqual(phase({stage: 'recovery_failed', timeline: [{stage: 'recover'}, {stage: 'recovery_verified'}]}).kind, 'failed');
assert.strictEqual(phase({stage: 'error', detail: 'some other error'}).label, '');
'''
    subprocess.run([node, '-e', script, str(helper)], check=True)
