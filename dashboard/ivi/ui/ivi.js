// Pure helpers for Ivi.qml (free of QML globals so tests can run them under node).
// Speed, rpm and gear are not shown here: the digital cluster above the strip already owns them.
// Route and map maths live in geo.js.

function clockText(date) {
    var h = date.getHours(), m = date.getMinutes()
    var h12 = h % 12 === 0 ? 12 : h % 12
    return (h < 12 ? "오전 " : "오후 ") + h12 + ":" + (m < 10 ? "0" : "") + m
}

// True when two JSON samples carry the same values, ignoring the timestamp/pid bookkeeping fields.
// Used so that an unchanged nav.json / status.json does not re-trigger bindings or repaints.
function sameSample(a, b) {
    if (!a || !b) return false
    var keys = {}, k
    for (k in a) keys[k] = 1
    for (k in b) keys[k] = 1
    for (k in keys) {
        if (k === "updated" || k === "pid") continue
        if (a[k] !== b[k]) return false
    }
    return true
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

// ---- radio panel (V1 build; visual only: there is no tuner hardware) ----
var RADIO_MIN = 87.5
var RADIO_MAX = 108.0
var RADIO_STATIONS = [
    { name: "Radio 1", freq: 93.1,  signal: 4 },
    { name: "Radio 2", freq: 96.5,  signal: 3 },
    { name: "Radio 3", freq: 99.9,  signal: 4 },
    { name: "Radio 4", freq: 103.3, signal: 2 },
    { name: "Radio 5", freq: 106.7, signal: 3 }
]

// Previous/next station with wrap-around; a stale index is clamped into range first.
function stepStation(index, dir) {
    var n = RADIO_STATIONS.length
    var i = index >= 0 && index < n ? index : 0
    return (i + dir + n) % n
}

function radioFreqText(freq) {
    return Number(freq).toFixed(1)
}

// Needle position along the dial scale: 0 at the low end, 1 at the high end.
function dialFraction(freq) {
    return Math.min(1, Math.max(0, (freq - RADIO_MIN) / (RADIO_MAX - RADIO_MIN)))
}
