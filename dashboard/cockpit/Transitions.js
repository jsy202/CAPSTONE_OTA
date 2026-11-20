// Ordered display events, independent of animation speed and telemetry polling frequency.
// `labels` maps a release version to its product name (3.3.0 -> "V2"); display only, unknown versions show as they are.
function versionLabel(version, labels) {
    return labels && labels[version] ? labels[version] : version;
}

function versionText(ota, labels) {
    if (!ota) return "—";
    var version = versionLabel(ota.version || "—", labels), candidate = ota.candidate ? versionLabel(ota.candidate, labels) : ota.candidate;
    if (ota.stage === "recovery_failed") return version;
    if (ota.stage === "committed" || ota.stage === "rolled_back") return version;
    if (!candidate || candidate === version) return version;
    if (ota.stage === "recover" || ota.stage === "recovery_verified" || ota.stage === "recheck")
        return candidate + " → " + version;
    return version + " → " + candidate;
}

// "V2 → V3" while updating, "V3 → V2" while rolling back, nothing at rest. Same wording as the alert strip.
// The direction comes from the versions themselves, not from which field the device filled: an update goes from the
// older to the newer version and a rollback the other way round (the device records version/candidate differently in
// different flows). A repair knows only its target version at first: then just that one is shown, never "— → V2".
function versionNumber(version) {
    var parts = String(version).split("."), n = 0
    for (var i = 0; i < 3; i++) n = n * 100000 + (parseInt(parts[i], 10) || 0)
    return n
}
function versionChange(ota, labels) {
    if (!ota || !ota.candidate) return "";
    var known = function (v) { return v && v !== "—" }
    if (!known(ota.version)) return versionLabel(ota.candidate, labels);
    if (ota.version === ota.candidate) return "";
    var older = versionNumber(ota.version) <= versionNumber(ota.candidate) ? ota.version : ota.candidate
    var newer = older === ota.version ? ota.candidate : ota.version
    var back = ota.stage === "recover" || ota.stage === "recovery_verified" || ota.stage === "recheck"
            || ota.stage === "rolled_back" || ota.stage === "recovery_failed" || ota.stage === "verification_failed";
    return back ? versionLabel(newer, labels) + " → " + versionLabel(older, labels)
                : versionLabel(older, labels) + " → " + versionLabel(newer, labels);
}

function consume(state, ota, labels) {
    if (!ota || !ota.timeline_id || !ota.timeline) return [];
    if (state.run !== ota.timeline_id) {
        state.run = ota.timeline_id;
        state.seen = 0;
        state.kinds = {};
        state.terminal = false;
        state.recoveryVerified = false;
    }
    var events = [];
    function add(kind, row) {
        if (state.kinds[kind] || state.terminal) return;
        state.kinds[kind] = true;
        var oldV = versionLabel(row.version || "—", labels), newV = versionLabel(row.candidate || ota.candidate || "", labels);
        events.push({kind: kind, transition: newV && newV !== oldV ? oldV + " → " + newV : oldV});
    }
    for (; state.seen < ota.timeline.length; state.seen++) {
        var row = ota.timeline[state.seen], s = row.stage;
        if (s === "metadata") add("preparing", row);
        // "trial" = the new image is written and switched in ("설치 완료"); "verify" = it runs ("업데이트 완료"). The
        // delay monitoring that starts there is supervision of the running update, shown by the fixed gauge, not an alert.
        else if (s === "trial" && !state.kinds.rollback) add("installed", row);
        else if (s === "verify" && !state.kinds.rollback) { add("installed", row); add("updated", row); }
        else if (s === "recover") add("rollback", row);
        else if (s === "recovery_verified") state.recoveryVerified = true;
        else if (s === "recovery_failed") {
            add(state.recoveryVerified ? "recovery_error" : "rejected", row);
            state.terminal = true;
        }
        else if (s === "rolled_back") { add("restored", row); state.terminal = true; }
        else if (s === "committed") {
            add("installed", row);
            add("updated", row);
            state.terminal = true;
        }
    }
    return events;
}

// The progress bar shows how far the CURRENT operation is, and nothing else. Only three operations have a bar:
// download (real bytes received), install (real bytes written) and rollback (real bytes of the retained image
// checked). Every other device stage (signature, trial, delay measurement...) shows no title and an empty bar;
// the delay measurement has its own readout below. `kind` picks the colour: update, rollback, done, blocked, failed.
// `historical` = a recorded stage replayed after the fact (the stage queue): it is over, so its bar is full.
function phase(ota, historical) {
    var idle = {label: "", fraction: 0, kind: "idle", percent: ""};
    if (!ota || !ota.stage) return idle;
    function pct(done, total) { return total > 0 ? Math.max(0, Math.min(1, done / total)) : 0; }
    function made(label, fraction, kind) { return {label: label, fraction: fraction, kind: kind, percent: Math.round(fraction * 100) + "%"}; }
    var stage = ota.stage, work = ota.work || null;
    if (historical && (stage === "download" || stage === "install" || stage === "recover"))
        return made(stage === "download" ? "다운로드" : stage === "install" ? "설치" : "롤백", 1, stage === "recover" ? "rollback" : "update");
    // Replayed rows of the two stages that follow a progress bar: show that bar full for its moment instead of an empty
    // gauge, so a viewer sees each bar reach 100% before it goes away. Live, these stages show no bar.
    if (historical && stage === "archive_verify") return made("다운로드", 1, "update");
    if (historical && stage === "trial") return made("설치", 1, "update");
    if (stage === "download") {
        var known = ota.total > 0 && ota.received !== undefined && ota.received !== null;
        return known ? made("다운로드", pct(ota.received, ota.total), "update") : {label: "다운로드", fraction: 0, kind: "update", percent: ""};
    }
    if (stage === "install") return work && work.kind === "install" ? made("설치", pct(work.done, work.total), "update") : made("설치", 0, "update");
    if (stage === "committed") return made("설치", 1, "done");
    if (stage === "recover") return work && work.kind === "rollback" ? made("롤백", pct(work.done, work.total), "rollback") : made("롤백", 0, "rollback");
    if (stage === "recovery_verified" || stage === "recheck") return made("롤백", 1, "rollback");
    if (stage === "rolled_back") return made("롤백", 1, "done");
    var refused = stage === "recovery_failed" || (stage === "error" && String(ota.detail || "").indexOf("recovery failed") === 0);
    if (refused) {
        // A refused recovery before the image checked out is the defence blocking a tampered image; after it, a real failure.
        var verified = false, rows = ota.timeline || [];
        for (var i = 0; i < rows.length; i++) if (rows[i].stage === "recovery_verified") verified = true;
        return made("롤백", 1, verified ? "failed" : "blocked");
    }
    return idle;
}

// The IVI is "responding" while its process exists and its data keeps being written: nav.json every 0.2 s (0.9 s of
// silence = stopped or restarting), or, without a nav file, status.json every 1 s. Otherwise the strip is covered
// instead of showing a frozen picture of the old screen.
function iviResponding(ivi) {
    if (!ivi || ivi.alive !== true) return false
    if (typeof ivi.nav_age_s === "number") return ivi.nav_age_s <= 0.9
    return typeof ivi.age_s === "number" && ivi.age_s <= 1.8
}

// The caption under the delay readout. A confirmed overrun does not roll back at once: the rollback waits for the
// vehicle to stand still, and says so while it waits; once the rollback itself runs the gauge tells the story.
function monitorStatus(pstate, stage) {
    if (pstate === "confirmed") return stage === "trial" || stage === "verify" ? "롤백을 위한 정차 대기 중" : ""
    if (pstate === "suspect") return "징후 포착 · 관찰 중"
    if (pstate === "observing") return ""
    return "모니터 신호 없음"
}

// The fixed gauge under the download/install/rollback gauge, "검증": how far the supervision of the CURRENT image has
// come. During the trial of a new image that is the Pi3 monitor's watch of the running update (`trialS` seconds); after a
// rollback it is the watch of the restored image (`recheckS` seconds). Progress = what the monitor really watched, never
// more than the time that has passed since the stage began (a monitor value left over from an earlier watch cannot fill it
// early). Unknown length or no reading = 0, nothing is claimed. At rest the gauge stays, with an empty bar.
function verification(ota, pi3, trialS, recheckS, now) {
    function made(fraction, kind) { return {label: "검증", fraction: fraction, kind: kind, percent: kind === "idle" ? "" : Math.round(fraction * 100) + "%"} }
    if (!ota || !ota.stage) return made(0, "idle")
    if (ota.stage === "rolled_back" || ota.stage === "committed") return made(1, "done")
    if (ota.stage === "recovery_verified") return made(0, "check")
    if (ota.stage !== "recheck" && ota.stage !== "verify") return made(0, "idle")
    var seconds = ota.stage === "verify" ? trialS : recheckS
    if (!(seconds > 0) || !pi3 || typeof pi3.monitored_s !== "number") return made(0, "check")
    var elapsed = typeof ota.updated === "number" && typeof now === "number" ? Math.max(0, now - ota.updated) : pi3.monitored_s
    return made(Math.min(1, Math.min(pi3.monitored_s, elapsed) / seconds), "check")
}

// The delay readout. While the cluster does not answer the socket (a restart: its cell says "no signal") there is no
// delay to read: how long unanswered frames have waited is not a delay of anything.
function delayReading(cluster, delayMs) {
    if (!cluster || delayMs === null || delayMs === undefined) return null
    return delayMs
}

// New rows of the feeder's log. Rows carry an increasing number `n`; the list itself is trimmed, so its length says
// nothing about what is new. The first look never replays history, and a feeder that restarted (numbers start over)
// starts again from where it is.
function freshLogRows(rows, seen) {
    // a row without a number (a preview, an older feeder) is numbered by its position, so the counter is always a number
    function number(row, index) { return typeof row.n === "number" ? row.n : index + 1 }
    var latest = rows.length ? number(rows[rows.length - 1], rows.length - 1) : 0
    if (seen < 0 || latest < seen) return {rows: [], seen: latest}
    var fresh = []
    for (var i = 0; i < rows.length; i++) if (number(rows[i], i) > seen) fresh.push(rows[i])
    return {rows: fresh, seen: latest}
}
