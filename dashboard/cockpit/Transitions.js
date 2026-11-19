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
function versionChange(ota, labels) {
    if (!ota || !ota.candidate) return "";
    var from = versionLabel(ota.version || "—", labels), to = versionLabel(ota.candidate, labels);
    if (from === to) return "";
    var back = ota.stage === "recover" || ota.stage === "recovery_verified" || ota.stage === "recheck"
            || ota.stage === "rolled_back" || ota.stage === "recovery_failed" || ota.stage === "verification_failed";
    return back ? to + " → " + from : from + " → " + to;
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
        else if (s === "verify" && !state.kinds.rollback) add("installed", row);
        else if (s === "recover") add("rollback", row);
        else if (s === "recovery_verified") state.recoveryVerified = true;
        else if (s === "recovery_failed") {
            add(state.recoveryVerified ? "recovery_error" : "rejected", row);
            state.terminal = true;
        }
        else if (s === "rolled_back") { add("restored", row); state.terminal = true; }
        else if (s === "committed") {
            add("installed", row);
            add("accepted", row);
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
