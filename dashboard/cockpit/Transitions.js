// Ordered display events, independent of animation speed and telemetry polling frequency.
function versionText(ota) {
    if (!ota) return "—";
    if (ota.stage === "recovery_failed") return "복구 실패";
    var version = ota.version || "—", candidate = ota.candidate;
    if (ota.stage === "committed" || ota.stage === "rolled_back") return version;
    if (!candidate || candidate === version) return version;
    if (ota.stage === "recover" || ota.stage === "recovery_verified" || ota.stage === "recheck")
        return candidate + " → " + version;
    return version + " → " + candidate;
}

function consume(state, ota) {
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
        var oldV = row.version || "—", newV = row.candidate || ota.candidate || "";
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
