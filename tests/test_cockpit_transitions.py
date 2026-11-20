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
// The stage replay shows each stage briefly: the bars must reach 100% before they go away.
assert.deepStrictEqual(phase({stage: 'archive_verify'}, true), {label: '다운로드', fraction: 1, kind: 'update', percent: '100%'});
assert.deepStrictEqual(phase({stage: 'trial'}, true), {label: '설치', fraction: 1, kind: 'update', percent: '100%'});
assert.strictEqual(phase({stage: 'trial'}).kind, 'idle');            // live: the long trial itself shows no install bar
assert.strictEqual(phase({stage: 'archive_verify'}).kind, 'idle');
assert.deepStrictEqual(phase({stage: 'idle', version: '3.5.0'}), {label: '', fraction: 0, kind: 'idle', percent: ''});   // nothing is happening: no gauge
assert.deepStrictEqual(consume({run: null, seen: 0, kinds: {}}, {stage: 'idle', version: '3.5.0'}), []);
assert.strictEqual(versionChange({stage: 'install', version: '2.0.0', candidate: '3.0.0'}, names), 'V2 → V3');
assert.strictEqual(versionChange({stage: 'recover', version: '2.0.0', candidate: '3.0.0'}, names), 'V3 → V2');
const labelled = consume({run: null, seen: 0, kinds: {}}, {timeline_id: 'n', timeline: [
    {stage: 'metadata', version: '2.0.0', candidate: '3.0.0'}, {stage: 'verify', version: '2.0.0', candidate: '3.0.0'}]}, names);
assert.deepStrictEqual(labelled.map(e => e.transition), ['V2 → V3', 'V2 → V3', 'V2 → V3']);
const state = {run: null, seen: 0, kinds: {}};
assert.deepStrictEqual(consume(state, {stage: 'committed', version: '2.0.0'}), []);
const stages = ['metadata', 'signature_verified', 'download', 'install', 'trial',
                'verify', 'verification_failed', 'recover', 'recovery_failed'];
const ota = {timeline_id: 'one', timeline: stages.map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))};
const events = consume(state, ota);
assert.deepStrictEqual(events.map(e => e.kind), ['preparing', 'installed', 'verifying', 'rollback', 'rejected']);
assert(events[1].transition.includes('2.0.0') && events[1].transition.includes('3.0.0'));
assert.deepStrictEqual(consume(state, ota), []);
ota.timeline.push({stage: 'committed', version: '3.0.0'});
assert.deepStrictEqual(consume(state, ota), []); // a terminal rejection cannot become success
const normal = consume(state, {timeline_id: 'two', timeline: stages.slice(0, 6).concat(
    ['committed']).map(stage => ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(normal.map(e => e.kind), ['preparing', 'installed', 'verifying', 'accepted']);
const rollback = consume(state, {timeline_id: 'three', timeline: stages.slice(0, -1).concat(
    ['recovery_verified', 'recheck', 'rolled_back']).map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(rollback.map(e => e.kind), ['preparing', 'installed', 'verifying', 'rollback', 'restored']);
const failure = consume(state, {timeline_id: 'four', timeline: ['recover', 'recovery_verified',
    'recovery_failed'].map(stage => ({stage}))});
assert.deepStrictEqual(failure.map(e => e.kind), ['rollback', 'recovery_error']);
console.log(JSON.stringify(events));
'''
    result = subprocess.run([node, '-e', script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 5


def test_real_qml_animations_show_all_the_bands_in_order(tmp_path):
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
            if (observed.length === 5) {
                console.log("BAND_ORDER:" + JSON.stringify(observed))
                cockpit.close(); Qt.quit()
            }
        }
    }
}
''' % json.dumps(cockpit))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=40,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                               'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
    assert done.returncode == 0, done.stderr
    assert 'BAND_ORDER:["preparing","installed","verifying","rollback","rejected"]' in done.stderr, done.stderr
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


def test_the_ivi_strip_is_marked_as_not_responding_while_its_status_is_stale_or_missing():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.strictEqual(iviResponding({alive: true, nav_age_s: 0.3, age_s: 0.4}), true);
assert.strictEqual(iviResponding({alive: true, nav_age_s: 0.8, age_s: 0.9}), true);    // load can delay a 0.2 s writer a little
assert.strictEqual(iviResponding({alive: true, nav_age_s: 1.2, age_s: 1.3}), false);   // restarting: nothing written for a while
assert.strictEqual(iviResponding({alive: false, nav_age_s: 0.1, age_s: 0.1}), false);  // the process is gone right now
assert.strictEqual(iviResponding({alive: true, age_s: 1.2}), true);                    // no nav file: the 1 s status is the only clock
assert.strictEqual(iviResponding({alive: true, age_s: 2.4}), false);
assert.strictEqual(iviResponding(null), false);                                         // no status file at all
assert.strictEqual(iviResponding({version: 'V2'}), false);                              // an old feeder without ages: do not claim it is up
'''
    result = subprocess.run([node, '-e', script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _node(script):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    prelude = "const fs = require('fs'), vm = require('vm'), assert = require('assert'); vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));\n"
    result = subprocess.run([node, '-e', prelude + script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_verifying_follows_installed_as_its_own_event_and_only_once():
    _node(r"""
const state = {run: null, seen: 0, kinds: {}};
const rows = s => ({timeline_id: 'v', timeline: s.map(stage => ({stage, version: '2.0.0', candidate: '3.0.0'}))});
const first = consume(state, rows(['metadata', 'install', 'trial', 'verify']));
assert.deepStrictEqual(first.map(e => e.kind), ['preparing', 'installed', 'verifying']);
assert.strictEqual(first[2].transition, '2.0.0 → 3.0.0');
assert.deepStrictEqual(consume(state, rows(['metadata', 'install', 'trial', 'verify', 'verify'])), []);   // not repeated
""")


def test_the_monitor_caption_says_what_the_confirmed_state_is_waiting_for():
    _node(r"""
assert.strictEqual(monitorStatus('confirmed', 'verify'), '롤백을 위한 정차 대기 중');
assert.strictEqual(monitorStatus('confirmed', 'trial'), '롤백을 위한 정차 대기 중');
assert.strictEqual(monitorStatus('confirmed', 'recover'), '');          // the rollback itself is running: no waiting caption
assert.strictEqual(monitorStatus('confirmed', 'rolled_back'), '');
assert.strictEqual(monitorStatus('suspect', 'verify'), '징후 포착 · 관찰 중');
assert.strictEqual(monitorStatus('observing', 'verify'), '');
assert.strictEqual(monitorStatus('lost', 'verify'), '모니터 신호 없음');
assert.strictEqual(monitorStatus('blind', ''), '모니터 신호 없음');
""")


def test_the_check_gauge_follows_the_real_monitored_time_after_a_rollback():
    _node(r"""
const pi3 = s => ({monitored_s: s});
const idle = {label: '', fraction: 0, kind: 'idle', percent: ''};
assert.deepStrictEqual(check({stage: 'verify'}, pi3(5), 20, 100), idle);
assert.deepStrictEqual(check({stage: 'recover'}, pi3(5), 20, 100), idle);
assert.deepStrictEqual(check({stage: 'recovery_verified'}, pi3(5), 20, 100), {label: '확인', fraction: 0, kind: 'check', percent: '0%'});
assert.strictEqual(check({stage: 'recheck', updated: 90}, pi3(5), 20, 100).fraction, 0.25);        // 5 s of 20 s watched
assert.strictEqual(check({stage: 'recheck', updated: 90}, pi3(500), 20, 100).fraction, 0.5);       // a stale big value cannot beat the 10 s that passed
assert.strictEqual(check({stage: 'recheck', updated: 70}, pi3(30), 20, 100).fraction, 1);
assert.strictEqual(check({stage: 'recheck'}, null, 20, 100).fraction, 0);                           // no monitor reading: nothing is claimed
assert.strictEqual(check({stage: 'recheck', updated: 90}, pi3(5), 0, 100).fraction, 0);             // unknown length: nothing is claimed
assert.deepStrictEqual(check({stage: 'rolled_back'}, pi3(0), 20, 100), {label: '확인', fraction: 1, kind: 'done', percent: '100%'});
""")


def test_new_log_rows_are_found_by_their_number_not_by_the_list_length():
    _node(r"""
const trimmed = (from, to) => Array.from({length: to - from + 1}, (_, i) => ({n: from + i, text: 'row ' + (from + i)}));
let r = freshLogRows(trimmed(1, 14), -1);                       // first look: history is never replayed
assert.deepStrictEqual([r.rows.length, r.seen], [0, 14]);
r = freshLogRows(trimmed(10, 23), 20);                          // the list is full (14 rows) yet three rows are new
assert.deepStrictEqual(r.rows.map(x => x.n), [21, 22, 23]);
assert.strictEqual(r.seen, 23);
r = freshLogRows(trimmed(10, 23), 23);
assert.deepStrictEqual([r.rows.length, r.seen], [0, 23]);       // nothing new
r = freshLogRows(trimmed(1, 3), 50);                            // the feeder restarted and counts from the start again
assert.deepStrictEqual([r.rows.length, r.seen], [0, 3]);
r = freshLogRows([], -1);
assert.deepStrictEqual([r.rows.length, r.seen], [0, 0]);
""")


def test_the_alert_strip_says_installed_then_verifying_and_only_a_passed_check_says_update_complete(tmp_path):
    """설치 완료 -> 검증 중 -> (검증이 통과하면) 업데이트 완료. Nothing says "update complete" before the check passed."""
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed for the alert strip check')
    cockpit = (Path(__file__).parents[1] / 'dashboard/cockpit/Cockpit.qml').as_uri()
    harness = tmp_path / 'Headlines.qml'
    harness.write_text('''import QtQuick 2.15
Item {
    property var cockpit
    property var band
    function find(item) {
        if (item.objectName === "otaEventBand") return item
        for (var i = 0; item.children && i < item.children.length; i++) { var f = find(item.children[i]); if (f) return f }
        return null
    }
    Component.onCompleted: {
        var component = Qt.createComponent(%s)
        cockpit = component.createObject(null, {dataUrl: "file:///nonexistent-cockpit-test-data"})
        if (!cockpit) throw new Error(component.errorString())
        band = find(cockpit.contentItem)
    }
    Timer {
        interval: 200; running: true; repeat: false
        onTriggered: {
            var seen = {}
            var kinds = ["preparing", "installed", "verifying", "accepted", "rollback"]
            for (var i = 0; i < kinds.length; i++) {
                band.pending = [{kind: kinds[i], transition: "V2 \\u2192 V3"}]
                band.next()
                seen[kinds[i]] = band.headline
            }
            console.log("HEADLINES:" + JSON.stringify(seen))
            cockpit.close(); Qt.quit()
        }
    }
}
''' % json.dumps(cockpit))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=40,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
    assert done.returncode == 0, done.stderr
    line = [row for row in done.stderr.splitlines() if 'HEADLINES:' in row][0]
    seen = json.loads(line.split('HEADLINES:', 1)[1])
    assert seen == {'preparing': '업데이트 시작', 'installed': '설치 완료', 'verifying': '검증 중',
                    'accepted': '업데이트 완료', 'rollback': '롤백 준비'}, seen


def test_cluster_and_ivi_cells_are_covered_only_while_the_verifying_alert_is_shown(tmp_path):
    """Verification starts: both cells go dark for as long as the white "검증 중" alert is up, and not for any other alert."""
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed for the cover check')
    cockpit = (Path(__file__).parents[1] / 'dashboard/cockpit/Cockpit.qml').as_uri()
    harness = tmp_path / 'Cover.qml'
    harness.write_text('''import QtQuick 2.15
Item {
    property var cockpit
    property var band
    property int step: 0
    property var seen: ({})
    function find(item) {
        if (item.objectName === "otaEventBand") return item
        for (var i = 0; item.children && i < item.children.length; i++) { var f = find(item.children[i]); if (f) return f }
        return null
    }
    Component.onCompleted: {
        var component = Qt.createComponent(%s)
        cockpit = component.createObject(null, {dataUrl: "file:///nonexistent-cockpit-test-data"})
        if (!cockpit) throw new Error(component.errorString())
        band = find(cockpit.contentItem)
    }
    Timer {
        interval: 300; running: true; repeat: true
        onTriggered: {
            step++
            if (step === 1) seen.before = cockpit.verifyCover
            else if (step === 2) band.enqueue({kind: "rollback", transition: "V2 \\\\u2192 V3"})
            else if (step === 3) { seen.rollbackAlert = cockpit.verifyCover; band.pending = []; }
            else if (step === 4) { band.pending = [{kind: "verifying", transition: "V2 \\\\u2192 V3"}]; band.next() }
            else if (step === 5) seen.verifying = cockpit.verifyCover
            else if (step === 20) { seen.after = cockpit.verifyCover; console.log("COVER:" + JSON.stringify(seen)); cockpit.close(); Qt.quit() }
        }
    }
}
''' % json.dumps(cockpit))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=40,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
    assert done.returncode == 0, done.stderr
    line = [row for row in done.stderr.splitlines() if 'COVER:' in row][0]
    seen = json.loads(line.split('COVER:', 1)[1])
    assert seen['before'] is False and seen['rollbackAlert'] is False
    assert seen['verifying'] is True                      # covered while the verifying alert is up
    assert seen['after'] is False                         # and uncovered again once it has gone


def test_log_rows_without_a_number_still_work_by_their_position():
    # A preview or an older feeder sends rows without `n`: the screen must never end up with an undefined counter.
    _node(r"""
let r = freshLogRows([{text: 'a'}, {text: 'b'}], -1);
assert.deepStrictEqual([r.rows.length, r.seen], [0, 2]);
r = freshLogRows([{text: 'a'}, {text: 'b'}, {text: 'c'}], 2);
assert.deepStrictEqual(r.rows.map(x => x.text), ['c']);
assert.strictEqual(r.seen, 3);
assert.strictEqual(typeof freshLogRows([], -1).seen, 'number');
""")
