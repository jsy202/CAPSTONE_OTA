// Pure helpers for Ivi.qml (free of QML globals so tests can run them under node).

function formatDistance(meters) {
    var m = Number(meters)
    if (!(m > 0)) return "0 m"
    if (m < 1000) return Math.round(m) + " m"
    return (m / 1000).toFixed(1) + " km"
}

// Length of a flat [x0, y0, x1, y1, ...] polyline, in the same unit as the points (metres).
function routeLength(flat) {
    if (!flat || flat.length < 4) return 0
    var total = 0
    for (var i = 2; i < flat.length; i += 2)
        total += Math.hypot(flat[i] - flat[i - 2], flat[i + 1] - flat[i - 1])
    return total
}

// The route is a loop: nav.py wraps the travelled distance with a modulo, so lap = completed laps + 1.
function lapProgress(distance, length) {
    if (!(length > 0)) return { lap: 0, fraction: 0, into: 0 }
    var d = Math.max(0, Number(distance) || 0)
    var into = d % length
    return { lap: Math.floor(d / length) + 1, fraction: into / length, into: into }
}

function remainingDistance(length, distance) {
    if (!(length > 0)) return 0
    return length - ((Math.max(0, Number(distance) || 0)) % length)
}

// Speed only comes from real CAN 0x200 frames; without a live bus (or before the first read) no number is shown.
function speedText(nav, canLive) {
    if (!canLive || !nav || typeof nav.speed !== "number") return "–"
    return String(Math.round(nav.speed))
}

function clockText(date) {
    var h = date.getHours(), m = date.getMinutes()
    var h12 = h % 12 === 0 ? 12 : h % 12
    return (h < 12 ? "오전 " : "오후 ") + h12 + ":" + (m < 10 ? "0" : "") + m
}

// Map zoom (screen px per metre): wider view at higher real speed, clamped.
function mapScale(speedKmh) {
    var v = Math.min(Math.max(Number(speedKmh) || 0, 0), 120)
    return 0.95 - 0.35 * v / 120
}

// ---- route geometry ----
function cumulative(flat) {
    var cum = [0]
    for (var i = 2; i < flat.length; i += 2)
        cum.push(cum[cum.length - 1] + Math.hypot(flat[i] - flat[i - 2], flat[i + 1] - flat[i - 1]))
    return cum
}

// Index of the segment (its start vertex) that contains distance d along the route.
function vertexAt(cum, d) {
    var lo = 0, hi = cum.length - 2
    if (hi < 0) return 0
    while (lo < hi) {
        var mid = (lo + hi + 1) >> 1
        if (cum[mid] <= d) lo = mid; else hi = mid - 1
    }
    return lo
}

function _bearing(flat, i) {   // degrees clockwise from +y (north), segment i -> i+1
    return Math.atan2(flat[2 * i + 2] - flat[2 * i], flat[2 * i + 3] - flat[2 * i + 1]) * 180 / Math.PI
}
function _norm(a) {
    while (a > 180) a -= 360
    while (a <= -180) a += 360
    return a
}

// Next turn ahead on the looping route: heading change of >= 25 degrees accumulated over <= 40 m.
// Returns { distance (m from the current position), angle (+ right / - left, degrees), direction } or null.
function nextTurn(flat, into) {
    if (!flat || flat.length < 6) return null
    var n = flat.length / 2, cum = cumulative(flat), L = cum[n - 1]
    if (!(L > 0)) return null
    var s = vertexAt(cum, into)
    var closed = Math.hypot(flat[0] - flat[2 * n - 2], flat[1] - flat[2 * n - 1]) < 5
    var order = []
    for (var v = s + 1; v <= n - 1; v++) order.push(v)
    for (var w = 1; w <= s; w++) order.push(w)
    var win = []
    for (var k = 0; k < order.length; k++) {
        var vi = order[k], turn = 0
        if (vi === n - 1) {
            if (closed) turn = _norm(_bearing(flat, 0) - _bearing(flat, n - 2))
        } else {
            turn = _norm(_bearing(flat, vi) - _bearing(flat, vi - 1))
        }
        var dist = vi > s ? cum[vi] - into : L - into + cum[vi]
        if (vi === n - 1) dist = cum[vi] - into
        win.push({ d: dist, t: turn })
        while (win.length && dist - win[0].d > 40) win.shift()
        var sum = 0, first = -1
        for (var j = 0; j < win.length; j++) {
            sum += win[j].t
            if (first < 0 && Math.abs(win[j].t) > 1e-9) first = j
        }
        if (Math.abs(sum) >= 25 && first >= 0)
            return { distance: Math.max(0, win[first].d), angle: sum, direction: sum > 0 ? "right" : "left" }
    }
    return null
}

// ---- music player panel (visual only: no audio backend on the board) ----
function formatTrackTime(sec) {
    var s = Math.max(0, Math.floor(Number(sec) || 0))
    return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60)
}

function stepTrack(index, count, dir) {
    if (!(count > 0)) return 0
    return (index + dir + count) % count
}

// One second of playback; reaching the end asks the caller for the next track.
function playerTick(elapsed, duration, playing) {
    if (!playing) return { elapsed: elapsed, ended: false }
    if (elapsed + 1 >= duration) return { elapsed: 0, ended: true }
    return { elapsed: elapsed + 1, ended: false }
}

function progressFraction(elapsed, duration) {
    if (!(duration > 0)) return 0
    return Math.min(1, Math.max(0, elapsed / duration))
}
