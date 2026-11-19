.pragma library
// Pure player logic for MusicPlayer.qml (no QML or Qt dependencies, node-testable).

function formatTime(seconds) {
    var s = Math.floor(Number(seconds));
    if (!isFinite(s) || s < 0) s = 0;
    var m = Math.floor(s / 60), r = s % 60;
    return m + ':' + (r < 10 ? '0' : '') + r;
}

function wrapIndex(i, count) {
    if (count <= 0) return 0;
    return ((i % count) + count) % count;
}

function clamp01(v) {
    return v < 0 ? 0 : (v > 1 ? 1 : v);
}

// Map a pointer x inside a bar (left, width) to a 0..1 ratio.
function ratioFromX(x, left, width) {
    if (!(width > 0)) return 0;
    return clamp01((x - left) / width);
}

// Advance playback by dt seconds. durations[i] is the length of track i.
// At the end of a track, move to the next one (wrapping) and restart at 0.
function advance(state, dt, durations) {
    var n = durations.length;
    if (n === 0) return {index: 0, position: 0};
    var index = wrapIndex(state.index, n);
    var pos = state.position + dt;
    if (pos >= durations[index]) return {index: wrapIndex(index + 1, n), position: 0};
    return {index: index, position: pos};
}

// Indices of the next `max` tracks after `current`, never including current.
function upNext(current, count, max) {
    var out = [];
    for (var k = 1; k < count && out.length < max; k++) out.push(wrapIndex(current + k, count));
    return out;
}
