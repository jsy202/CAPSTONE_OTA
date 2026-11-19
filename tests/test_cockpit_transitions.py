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
assert.strictEqual(phase({stage: 'verify'}).label, '지연 측정 중');
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


def test_gauge_phase_names_each_step_and_uses_real_download_bytes():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.deepStrictEqual(phase(null), {label: '대기', fraction: 0, kind: 'idle', percent: ''});
const half = phase({stage: 'download', received: 100, total: 200});
assert.strictEqual(half.label, '파일 받는 중'); assert.strictEqual(half.percent, '50%'); assert.strictEqual(half.kind, 'update');
const none = phase({stage: 'download'});            // size unknown: no invented percentage
assert.strictEqual(none.percent, '');
const early = phase({stage: 'download', received: 0, total: 200}).fraction;
const late = phase({stage: 'download', received: 200, total: 200}).fraction;
assert(early < late && late < phase({stage: 'install'}).fraction);   // the gauge only moves forward through an update
assert.strictEqual(phase({stage: 'trial'}).label, '시운전 중');
const back = phase({stage: 'recover'});
assert.strictEqual(back.kind, 'rollback'); assert.strictEqual(back.label, '롤백 중 · 이전 버전으로');
assert.strictEqual(phase({stage: 'recovery_verified'}).kind, 'rollback');
assert.strictEqual(phase({stage: 'recovery_verified'}).label, '이전 버전 검사 통과');
assert.strictEqual(phase({stage: 'rolled_back'}).label, '롤백 완료');
assert.strictEqual(phase({stage: 'rolled_back'}).kind, 'done');
assert.strictEqual(phase({stage: 'recovery_failed'}).kind, 'failed');
assert.strictEqual(phase({stage: 'committed'}).fraction, 1);
assert.strictEqual(phase({stage: 'something_new'}).kind, 'update');   // unknown stage is shown, not hidden
assert.strictEqual(phase({stage: 'something_new'}).label, '진행 중');  // never a raw identifier
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
const refused = phase({stage: 'error', detail: 'recovery failed; no successful recovery is claimed'});
assert.strictEqual(refused.label, '복구 거부 · 실행 차단');
assert.strictEqual(phase({stage: 'recovery_failed'}).label, '복구 거부 · 실행 차단');
assert.strictEqual(phase({stage: 'service_stopped_after_recovery_failure'}).label, '실패한 앱 중지됨'); assert.strictEqual(refused.kind, 'failed');
assert.strictEqual(phase({stage: 'service_stopped_after_recovery_failure'}).kind, 'failed');
assert.strictEqual(phase({stage: 'error', detail: 'disk full'}).label, '오류');   // a different error stays a plain error
assert.strictEqual(phase({stage: 'download_and_verify'}).kind, 'update');
assert(!/[a-z]_[a-z]/.test(phase({stage: 'download_and_verify'}).label));         // never a raw stage name on screen
'''
    subprocess.run([node, '-e', script, str(helper)], check=True)
