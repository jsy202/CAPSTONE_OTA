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
assert.deepStrictEqual(labelled.map(e => e.transition), ['V2 → V3', 'V2 → V3', 'V2 → V3']);   // verify alone still yields the whole sequence
const state = {run: null, seen: 0, kinds: {}};
assert.deepStrictEqual(consume(state, {stage: 'committed', version: '2.0.0'}), []);
const stages = ['metadata', 'signature_verified', 'download', 'install', 'trial',
                'verify', 'verification_failed', 'recover', 'recovery_failed'];
const ota = {timeline_id: 'one', timeline: stages.map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))};
const events = consume(state, ota);
assert.deepStrictEqual(events.map(e => e.kind), ['preparing', 'installed', 'updated', 'rejected']);
assert(events[1].transition.includes('2.0.0') && events[1].transition.includes('3.0.0'));
assert.deepStrictEqual(consume(state, ota), []);
ota.timeline.push({stage: 'committed', version: '3.0.0'});
assert.deepStrictEqual(consume(state, ota), []); // a terminal rejection cannot become success
const normal = consume(state, {timeline_id: 'two', timeline: stages.slice(0, 6).concat(
    ['committed']).map(stage => ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(normal.map(e => e.kind), ['preparing', 'installed', 'updated']);
const rollback = consume(state, {timeline_id: 'three', timeline: stages.slice(0, -1).concat(
    ['recovery_verified', 'recheck', 'rolled_back']).map(stage =>
    ({stage, version: '2.0.0', candidate: '3.0.0'}))});
assert.deepStrictEqual(rollback.map(e => e.kind), ['preparing', 'installed', 'updated', 'restored']);
const failure = consume(state, {timeline_id: 'four', timeline: ['recover', 'recovery_verified',
    'recovery_failed'].map(stage => ({stage}))});
assert.deepStrictEqual(failure.map(e => e.kind), ['recovery_error']);
console.log(JSON.stringify(events));
'''
    result = subprocess.run([node, '-e', script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 4


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
            if (observed.length === 4) {
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
    assert 'BAND_ORDER:["preparing","installed","updated","rejected"]' in done.stderr, done.stderr
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


def test_installed_comes_with_the_switch_and_updated_when_the_new_image_runs_and_nothing_says_verifying():
    """설치 완료 = the new image is switched in (stage trial); 업데이트 완료 = it runs (stage verify). There is no 검증 중
    alert: the delay monitoring that follows is supervision of the running update, shown by the fixed gauge instead."""
    _node(r"""
const state = {run: null, seen: 0, kinds: {}};
const rows = s => ({timeline_id: 'v', timeline: s.map(stage => ({stage, version: '2.0.0', candidate: '3.0.0'}))});
let first = consume(state, rows(['metadata', 'install', 'trial']));
assert.deepStrictEqual(first.map(e => e.kind), ['preparing', 'installed']);
let next = consume(state, rows(['metadata', 'install', 'trial', 'verify']));
assert.deepStrictEqual(next.map(e => e.kind), ['updated']);
assert.deepStrictEqual(consume(state, rows(['metadata', 'install', 'trial', 'verify', 'verify'])), []);   // never repeated
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


def test_the_fixed_verification_gauge_fills_at_once_when_the_key_and_image_check_passes():
    """검증 = the signature and image check only. It is empty until that check passes and then simply full. It is NOT a
    progress of the delay monitoring. The latest verification of a run wins: an update's check, then a rollback's check."""
    _node(r"""
const rows = (...stages) => ({stage: stages[stages.length - 1], timeline: stages.map(stage => ({stage}))});
const empty = {label: '검증', fraction: 0, kind: 'idle', percent: ''};
const full = {label: '검증', fraction: 1, kind: 'done', percent: '100%'};
assert.deepStrictEqual(verified(null), empty);
assert.deepStrictEqual(verified({stage: 'download'}), empty);
assert.deepStrictEqual(verified(rows('metadata', 'signature', 'signature_verified', 'download')), empty);   // not checked yet
assert.deepStrictEqual(verified(rows('metadata', 'download', 'archive_verify')), full);                    // the image matched
assert.deepStrictEqual(verified(rows('metadata', 'download', 'archive_verify', 'install', 'trial', 'verify')), full);
// a refused rollback: the recovery image check did not pass, so the gauge is empty again (the defence held)
assert.deepStrictEqual(verified(rows('metadata', 'download', 'archive_verify', 'install', 'trial', 'verify', 'recover', 'recovery_failed')), empty);
// a rollback whose image check passed
assert.deepStrictEqual(verified(rows('metadata', 'download', 'archive_verify', 'trial', 'verify', 'recover', 'recovery_verified', 'recheck')), full);
assert.deepStrictEqual(verified(rows('recover')), empty);                                                    // a repair run: its own check first
assert.deepStrictEqual(verified({stage: 'install'}), full);                                                // no timeline: the stage says it
assert.deepStrictEqual(verified({stage: 'recovery_verified'}), full);
// the delay monitoring has nothing to do with it
assert.deepStrictEqual(verified({stage: 'verify', work: null, timeline: [{stage: 'download'}]}), empty);
""")


def test_the_watch_gauge_after_a_rollback_follows_the_real_monitored_time():
    _node(r"""
const pi3 = s => ({monitored_s: s});
const idle = {label: '확인', fraction: 0, kind: 'idle', percent: ''};
assert.deepStrictEqual(watch({stage: 'verify'}, pi3(5), 20, 100), idle);
assert.deepStrictEqual(watch({stage: 'recover'}, pi3(5), 20, 100), idle);
assert.deepStrictEqual(watch({stage: 'recovery_verified'}, pi3(5), 20, 100), {label: '확인', fraction: 0, kind: 'check', percent: '0%'});
assert.strictEqual(watch({stage: 'recheck', updated: 90}, pi3(5), 20, 100).fraction, 0.25);        // 5 s of 20 s watched
assert.strictEqual(watch({stage: 'recheck', updated: 90}, pi3(500), 20, 100).fraction, 0.5);       // a stale big value cannot beat the 10 s that passed
assert.strictEqual(watch({stage: 'recheck', updated: 70}, pi3(30), 20, 100).fraction, 1);
assert.strictEqual(watch({stage: 'recheck'}, null, 20, 100).fraction, 0);                           // no monitor reading: nothing is claimed
assert.strictEqual(watch({stage: 'recheck', updated: 90}, pi3(5), 0, 100).fraction, 0);             // unknown length: nothing is claimed
assert.deepStrictEqual(watch({stage: 'rolled_back'}, pi3(0), 20, 100), {label: '확인', fraction: 1, kind: 'done', percent: '100%'});
""")


def test_the_delay_readout_is_unknown_while_the_cluster_does_not_answer():
    # The cluster is off for a restart (the cell says "no signal"): a count of how long its frames wait is not a delay.
    _node(r"""
assert.strictEqual(delayReading(null, 2900), null);
assert.strictEqual(delayReading(undefined, 2900), null);
assert.strictEqual(delayReading({valid: true}, 180), 180);
assert.strictEqual(delayReading({valid: true}, null), null);
assert.strictEqual(delayReading({valid: true}, undefined), null);
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


def test_the_alert_strip_says_installed_then_update_complete(tmp_path):
    """설치 완료 (switched in) -> 업데이트 완료 (the new image runs); there is no 검증 중 alert."""
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
            var kinds = ["preparing", "installed", "updated", "rollback", "rejected", "port_closed"]
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
    assert seen == {'preparing': '업데이트 시작', 'installed': '설치 완료',
                    'updated': '업데이트 완료', 'rollback': '롤백 준비', 'rejected': '롤백 차단', 'port_closed': '디버깅 포트 차단'}, seen



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


def test_a_rollback_always_reads_newer_to_older_whatever_order_the_device_recorded_the_versions_in():
    # 2026-11-20: the gauge said "롤백 V2 -> V3" while the alert said "V3 -> V2": the device records version/candidate
    # differently in different flows, and the repair flow knows its current version only later ("— -> V2").
    _node(r"""
const names = {'3.9.0': 'V2', '3.10.0': 'V3'};
const change = (stage, version, candidate) => versionChange({stage, version, candidate}, names);
assert.strictEqual(change('recover', '3.9.0', '3.10.0'), 'V3 → V2');
assert.strictEqual(change('recover', '3.10.0', '3.9.0'), 'V3 → V2');             // fields the other way round
assert.strictEqual(change('recheck', '3.10.0', '3.9.0'), 'V3 → V2');
assert.strictEqual(change('rolled_back', '3.10.0', '3.9.0'), 'V3 → V2');
assert.strictEqual(change('install', '3.9.0', '3.10.0'), 'V2 → V3');             // an update goes older -> newer
assert.strictEqual(change('download', '3.9.0', '3.10.0'), 'V2 → V3');
assert.strictEqual(change('download', null, '3.9.0'), 'V2');                      // the repair knows only its target: no "— → V2"
assert.strictEqual(change('download', '—', '3.9.0'), 'V2');
assert.strictEqual(change('committed', '3.9.0', null), '');                       // nothing to show at rest
assert.strictEqual(change('download', '3.9.0', '3.9.0'), '');
""")


def test_the_rollback_notice_comes_with_the_decision_not_with_the_switch_to_the_old_version():
    # 롤백 준비 used to pop up when the device began the rollback, just before it went to V2. The decision is made earlier:
    # the monitor confirms the overrun and the rollback waits for a standstill. The notice belongs there, once per run.
    _node(r"""
const names = {'3.9.0': 'V2', '3.10.0': 'V3'};
const ota = (stage, id = 'run1') => ({stage, version: '3.9.0', candidate: '3.10.0', timeline_id: id});
const notice = (p, o, done = '') => rollbackNotice(p, o, names, done);
assert.strictEqual(notice('observing', ota('verify')), null);
assert.strictEqual(notice('suspect', ota('verify')), null);
assert.deepStrictEqual(notice('confirmed', ota('verify')), {kind: 'rollback', transition: 'V2 → V3', run: 'run1'});
assert.deepStrictEqual(notice('confirmed', ota('trial')), {kind: 'rollback', transition: 'V2 → V3', run: 'run1'});
assert.strictEqual(notice('confirmed', ota('verify'), 'run1'), null);            // once per run
assert.notStrictEqual(notice('confirmed', ota('verify', 'run2'), 'run1'), null); // a new run may announce again
assert.strictEqual(notice('confirmed', ota('recover')), null);                   // too late: the rollback itself is running
assert.strictEqual(notice('confirmed', ota('rolled_back')), null);
assert.strictEqual(notice('confirmed', null), null);
assert.strictEqual(notice('confirmed', {stage: 'verify'}), null);                // no run id: nothing to key it on
""")


def test_the_rollback_notice_pops_up_when_the_monitor_confirms_and_only_once(tmp_path):
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed for the notice check')
    cockpit = (Path(__file__).parents[1] / 'dashboard/cockpit/Cockpit.qml').as_uri()
    harness = tmp_path / 'Notice.qml'
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
    function feed(state, stage) {
        return {now: 1, version_labels: {"3.9.0": "V2", "3.10.0": "V3"}, tap: null, cluster: null, ivi_ui: "", log: [],
                pi3: {state: state, latency_limit_ms: 100}, ota: {stage: stage, version: "3.9.0", candidate: "3.10.0", timeline_id: "run1",
                timeline: [{stage: "metadata"}, {stage: "trial"}, {stage: "verify"}]}}
    }
    Component.onCompleted: {
        var component = Qt.createComponent(%s)
        cockpit = component.createObject(null, {dataUrl: "file:///nonexistent-cockpit-test-data"})
        if (!cockpit) throw new Error(component.errorString())
        band = find(cockpit.contentItem)
        cockpit.d = feed("observing", "verify")
    }
    Timer {
        interval: 250; running: true; repeat: true
        onTriggered: {
            step++
            if (step === 1) { band.pending = []; cockpit.d = feed("confirmed", "verify") }
            else if (step === 2) { seen.kind = band.kind; seen.headline = band.headline; seen.sub = band.sub; cockpit.d = feed("confirmed", "verify") }
            else if (step === 3) { seen.queued_again = band.pending.length }
            else if (step === 4) { console.log("NOTICE:" + JSON.stringify(seen)); cockpit.close(); Qt.quit() }
        }
    }
}
''' % json.dumps(cockpit))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=40,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software', 'QML_XHR_ALLOW_FILE_READ': '1'})
    assert done.returncode == 0, done.stderr
    line = [row for row in done.stderr.splitlines() if 'NOTICE:' in row][0]
    seen = json.loads(line.split('NOTICE:', 1)[1])
    assert seen['kind'] == 'rollback' and seen['headline'] == '롤백 준비' and seen['sub'] == 'V3 → V2'
    assert seen['queued_again'] == 0


def test_update_start_alert_waits_until_the_target_version_is_known():
    """The device writes `metadata` before it knows the candidate (that comes with `signature_verified`). The alert used
    to be made at once and read `V2` or `V2 → V3` depending on when the screen looked."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed to exercise the QML JavaScript helper')
    helper = Path(__file__).parents[1] / 'dashboard/cockpit/Transitions.js'
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const names = {'3.9.0': 'V2', '3.10.0': 'V3'};
const state = {run: null, seen: 0, kinds: {}};
const timeline = [{stage: 'metadata', version: '3.9.0'}];
assert.deepStrictEqual(consume(state, {timeline_id: 'a', timeline, candidate: '3.10.0'}, names), []);   // target not in the record yet
timeline.push({stage: 'signature_verified', version: '3.9.0', candidate: '3.10.0'});
const events = consume(state, {timeline_id: 'a', timeline}, names);
assert.deepStrictEqual(events.map(e => [e.kind, e.transition]), [['preparing', 'V2 → V3']]);
timeline.push({stage: 'download', version: '3.9.0', candidate: '3.10.0'});
assert.deepStrictEqual(consume(state, {timeline_id: 'a', timeline}, names), []);                          // once only
const fresh = {run: null, seen: 0, kinds: {}};
const whole = consume(fresh, {timeline_id: 'b', timeline: timeline.slice()}, names);
assert.deepStrictEqual(whole.map(e => [e.kind, e.transition]), [['preparing', 'V2 → V3']]);
console.log('ok');
'''
    result = subprocess.run([node, '-e', script, str(helper)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
