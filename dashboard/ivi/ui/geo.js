.pragma library
// Route maths for MapView.qml. Plain JS, no QML types, so tests/test_ivi_geo.py can run it under node.
// Units: metres east (x) / north (y) of the map origin; headings and angles in degrees,
// clockwise from north; a positive turn angle means a right turn.

var TURN_MIN_DEG = 25     // heading change that counts as a turn
var TURN_WINDOW_M = 15    // heading is measured over this much route before and after a vertex

function norm180(a) {
    a = a % 360
    if (a > 180) a -= 360
    if (a <= -180) a += 360
    return a
}

function bearing(x0, y0, x1, y1) {
    return Math.atan2(x1 - x0, y1 - y0) * 180 / Math.PI
}

// Largest i in [0, n-2] with cum[i] <= d (clamped), by bisection.
function indexAt(cum, d) {
    var lo = 0, hi = cum.length - 2
    if (hi < 0) return 0
    while (lo < hi) {
        var mid = (lo + hi + 1) >> 1
        if (cum[mid] <= d) lo = mid; else hi = mid - 1
    }
    return lo
}

function pointAt(rt, d) {
    if (rt.closed) d = ((d % rt.length) + rt.length) % rt.length
    else d = Math.max(0, Math.min(rt.length, d))
    var i = indexAt(rt.cum, d), p = rt.p
    var span = rt.cum[i + 1] - rt.cum[i]
    var t = span > 0 ? (d - rt.cum[i]) / span : 0
    return [p[2 * i] + (p[2 * i + 2] - p[2 * i]) * t, p[2 * i + 1] + (p[2 * i + 3] - p[2 * i + 1]) * t]
}

function findTurns(rt) {
    var n = rt.cum.length, W = TURN_WINDOW_M, cand = []
    // The closing vertex of a loop duplicates vertex 0, so a loop scans 0..n-2 and an open route 1..n-2.
    for (var i = rt.closed ? 0 : 1; i < n - 1; i++) {
        var d = rt.cum[i]
        if (!rt.closed && (d < W || d > rt.length - W)) continue
        var a = pointAt(rt, d - W), b = pointAt(rt, d + W), v = [rt.p[2 * i], rt.p[2 * i + 1]]
        if (Math.hypot(a[0] - v[0], a[1] - v[1]) < 1e-6 || Math.hypot(b[0] - v[0], b[1] - v[1]) < 1e-6) continue
        var bin = bearing(a[0], a[1], v[0], v[1]), bout = bearing(v[0], v[1], b[0], b[1])
        var delta = norm180(bout - bin)
        if (Math.abs(delta) > TURN_MIN_DEG) cand.push({ i: i, d: d, bin: bin, bout: bout, delta: delta })
    }
    // Neighbouring candidates of the same sign are one bend sampled by several vertices.
    var turns = [], k = 0
    while (k < cand.length) {
        var g = k, best = k
        while (g + 1 < cand.length && cand[g + 1].i === cand[g].i + 1 && cand[g + 1].d - cand[g].d <= 2 * W && cand[g + 1].delta * cand[k].delta > 0) {
            g++
            if (Math.abs(cand[g].delta) > Math.abs(cand[best].delta)) best = g
        }
        // Bend angle: heading 2 windows before the first vertex vs. 2 windows after the last one.
        var s0 = pointAt(rt, cand[k].d - 2 * W), s1 = pointAt(rt, cand[g].d + 2 * W)
        var v0 = [rt.p[2 * cand[k].i], rt.p[2 * cand[k].i + 1]], v1 = [rt.p[2 * cand[g].i], rt.p[2 * cand[g].i + 1]]
        turns.push({ d: cand[best].d, angle: norm180(bearing(v1[0], v1[1], s1[0], s1[1]) - bearing(s0[0], s0[1], v0[0], v0[1])) })
        k = g + 1
    }
    return turns
}

// flat: [x0, y0, x1, y1, ...]; returns null when there is no usable route.
function buildRoute(flat) {
    if (!flat || flat.length < 4) return null
    var n = flat.length >> 1, cum = [0]
    for (var i = 1; i < n; i++)
        cum.push(cum[i - 1] + Math.hypot(flat[2 * i] - flat[2 * i - 2], flat[2 * i + 1] - flat[2 * i - 1]))
    if (!(cum[n - 1] > 0)) return null
    var rt = { p: flat, n: n, cum: cum, length: cum[n - 1],
               closed: Math.hypot(flat[0] - flat[2 * n - 2], flat[1] - flat[2 * n - 1]) < 1 }
    rt.turns = findTurns(rt)
    return rt
}

// Distance along the route of the point nearest to (x, y).
function project(rt, x, y) {
    var best = 1e30, bestD = 0, p = rt.p
    for (var i = 0; i < rt.n - 1; i++) {
        var x0 = p[2 * i], y0 = p[2 * i + 1], sx = p[2 * i + 2] - x0, sy = p[2 * i + 3] - y0
        var len2 = sx * sx + sy * sy
        var t = len2 > 0 ? Math.max(0, Math.min(1, ((x - x0) * sx + (y - y0) * sy) / len2)) : 0
        var dx = x0 + sx * t - x, dy = y0 + sy * t - y, dd = dx * dx + dy * dy
        if (dd < best) { best = dd; bestD = rt.cum[i] + Math.sqrt(len2) * t }
    }
    return bestD
}

// along: distance travelled on this lap; remaining: to the end of the loop;
// turn: the next real turn ({distance, angle}) or null if the route has none.
function routeInfo(rt, nav) {
    if (!rt || !nav) return null
    var along
    if (typeof nav.distance === "number" && isFinite(nav.distance)) along = ((nav.distance % rt.length) + rt.length) % rt.length
    else along = project(rt, nav.x, nav.y)
    var turn = null, t = rt.turns
    for (var i = 0; i < t.length; i++) {
        if (t[i].d > along + 1e-6) { turn = { distance: t[i].d - along, angle: t[i].angle }; break }
    }
    if (!turn && t.length) turn = { distance: t[0].d + rt.length - along, angle: t[0].angle }
    return { along: along, remaining: rt.length - along, turn: turn }
}

function formatDistance(m) {
    if (typeof m !== "number" || !isFinite(m)) return "--"
    if (m < 0) m = 0
    if (m >= 995) return (m / 1000).toFixed(1) + "km"
    return (Math.round(m / 10) * 10) + "m"
}

// ---- optional road names (the shipped map has none; only draw what the data really carries) ----
function namedRoads(roads) {
    var out = []
    for (var i = 0; roads && i < roads.length; i++) {
        var nm = roads[i].n || roads[i].name
        if (nm) out.push({ n: nm, p: roads[i].p })
    }
    return out
}

function nearestName(named, x, y, maxDist) {
    var best = maxDist * maxDist, name = ""
    for (var r = 0; r < named.length; r++) {
        var p = named[r].p
        for (var i = 0; i + 3 < p.length; i += 2) {
            var sx = p[i + 2] - p[i], sy = p[i + 3] - p[i + 1], len2 = sx * sx + sy * sy
            var t = len2 > 0 ? Math.max(0, Math.min(1, ((x - p[i]) * sx + (y - p[i + 1]) * sy) / len2)) : 0
            var dx = p[i] + sx * t - x, dy = p[i + 1] + sy * t - y, dd = dx * dx + dy * dy
            if (dd <= best) { best = dd; name = named[r].n }
        }
    }
    return name
}
