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

// What the big progress gauge shows for one telemetry stage. `kind` picks the colour: update (orange),
// rollback (red), done (green), failed (red, stays). Download uses the real byte counts when the device sent them.
var PHASES = {
    download_and_verify: ["업데이트 준비", 0.04, "update"], metadata: ["업데이트 정보 확인", 0.08, "update"], signature: ["서명 확인 중", 0.16, "update"],
    signature_verified: ["서명 확인 완료", 0.22, "update"], archive_verify: ["받은 파일 검사", 0.46, "update"],
    install: ["설치 중", 0.58, "update"], trial: ["시운전 중", 0.72, "update"], verify: ["지연 측정 중", 0.85, "update"],
    verification_failed: ["검증 실패", 0.85, "rollback"], recover: ["롤백 중 · 이전 버전으로", 0.3, "rollback"],
    recovery_verified: ["이전 버전 검사 통과", 0.65, "rollback"], recheck: ["롤백 후 재검증", 0.85, "rollback"],
    rolled_back: ["롤백 완료", 1, "done"], committed: ["업데이트 완료", 1, "done"],
    recovery_failed: ["복구 거부 · 실행 차단", 0.6, "failed"], failed: ["업데이트 실패", 0.4, "failed"],
    service_stopped_after_recovery_failure: ["실패한 앱 중지됨", 0.6, "failed"],
    error: ["오류", 0.3, "failed"]
};

function phase(ota) {
    if (!ota || !ota.stage) return {label: "대기", fraction: 0, kind: "idle", percent: ""};
    if (ota.stage === "download") {
        var known = ota.total > 0 && ota.received !== undefined && ota.received !== null;
        var ratio = known ? Math.max(0, Math.min(1, ota.received / ota.total)) : 0.3;
        return {label: "파일 받는 중", fraction: 0.22 + 0.2 * ratio, kind: "update",
                percent: known ? Math.round(ratio * 100) + "%" : ""};
    }
    // The device ends a refused recovery with a generic `error`; the detail says what it really was.
    if (ota.stage === "error" && String(ota.detail || "").indexOf("recovery failed") === 0)
        return {label: PHASES.recovery_failed[0], fraction: PHASES.recovery_failed[1], kind: "failed", percent: ""};
    var row = PHASES[ota.stage];
    if (!row) return {label: "진행 중", fraction: 0.5, kind: "update", percent: ""};
    return {label: row[0], fraction: row[1], kind: row[2], percent: ""};
}
