// Vector icon set on one 48x48 grid with one round stroke (4 units by default). draw(ctx, kind, ink, knock, lw):
// ink = icon colour, knock = colour of cut-outs (the surface the icon sits on).

var _lw = 4   // stroke width in grid units; draw() sets it per call so the rendered weight is the same at every size
function _stroke(c, ink) { c.strokeStyle = ink; c.fillStyle = ink; c.lineWidth = _lw; c.lineCap = "round"; c.lineJoin = "round" }
function _poly(c, pts, close) {
    c.beginPath(); c.moveTo(pts[0], pts[1])
    for (var i = 2; i < pts.length; i += 2) c.lineTo(pts[i], pts[i + 1])
    if (close) c.closePath()
}
function _circle(c, x, y, r) { c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2) }
function _rrect(c, x, y, w, h, r) {
    c.beginPath(); c.moveTo(x + r, y); c.lineTo(x + w - r, y); c.arcTo(x + w, y, x + w, y + r, r)
    c.lineTo(x + w, y + h - r); c.arcTo(x + w, y + h, x + w - r, y + h, r); c.lineTo(x + r, y + h)
    c.arcTo(x, y + h, x, y + h - r, r); c.lineTo(x, y + r); c.arcTo(x, y, x + r, y, r); c.closePath()
}
function _fan(c, cx, cy, r, ink, knock) {
    c.fillStyle = ink
    for (var i = 0; i < 3; i++) {
        c.save(); c.translate(cx, cy); c.rotate(i * 2 * Math.PI / 3)
        c.beginPath(); c.moveTo(0, 0)
        c.bezierCurveTo(-r * 0.55, -r * 0.15, -r * 0.95, -r * 0.85, -r * 0.1, -r)
        c.bezierCurveTo(r * 0.55, -r * 0.9, r * 0.45, -r * 0.3, 0, 0)
        c.fill(); c.restore()
    }
    c.fillStyle = knock; _circle(c, cx, cy, r * 0.17); c.fill()
}
function _seat(c, ink) {
    _stroke(c, ink)
    _poly(c, [14, 8, 24, 28, 42, 32], false); c.stroke()
    _poly(c, [37, 32, 37, 42], false); c.stroke()
}
function _zig(c, x, top, bottom) {   // wavy rising arrow used by the defrost / airflow icons
    var mid = (top + bottom) / 2
    c.beginPath(); c.moveTo(x, bottom); c.lineTo(x + 3, bottom - (bottom - top) / 4)
    c.lineTo(x - 3, mid); c.lineTo(x, top); c.stroke()
}

function draw(c, kind, ink, knock, lw) {
    _lw = lw > 0 ? lw : 4
    _stroke(c, ink)
    var i
    switch (kind) {
    case "search":
        _circle(c, 21, 21, 13); c.stroke(); _poly(c, [31, 31, 42, 42], false); c.stroke(); break
    case "bell":
        c.beginPath(); c.moveTo(9, 36); c.lineTo(9, 34); c.lineTo(13, 28); c.lineTo(13, 20)
        c.arc(24, 20, 11, Math.PI, 0); c.lineTo(35, 28); c.lineTo(39, 34); c.lineTo(39, 36); c.closePath(); c.fill()
        _circle(c, 24, 40, 4); c.fill(); break
    case "lte":
        for (i = 0; i < 4; i++) {
            c.fillStyle = i < 3 ? ink : "#5a5e68"
            _rrect(c, 8 + i * 9, 40 - (10 + i * 8), 6, 10 + i * 8, 2); c.fill()
        }
        break
    case "volume":
        _poly(c, [5, 18, 15, 18, 25, 8, 25, 40, 15, 30, 5, 30], true); c.fill()
        c.beginPath(); c.arc(25, 24, 9, -0.9, 0.9); c.stroke()
        c.beginPath(); c.arc(25, 24, 17, -0.9, 0.9); c.stroke(); break
    case "bluetooth":
        _poly(c, [13, 16, 33, 32, 23, 42, 23, 6, 33, 16, 13, 32], false); c.stroke(); break
    case "person":
        c.fillStyle = "#3a3d45"; _circle(c, 24, 24, 23); c.fill()
        c.fillStyle = "#c9ccd3"; _circle(c, 24, 18, 7); c.fill()
        c.beginPath(); c.moveTo(11, 38); c.arc(24, 38, 13, Math.PI, 0); c.closePath(); c.fill(); break
    case "fuel":
        _rrect(c, 8, 8, 20, 32, 3); c.stroke()
        c.fillStyle = ink; _rrect(c, 13, 13, 10, 9, 1); c.fill()
        c.beginPath(); c.moveTo(28, 18); c.lineTo(35, 18); c.lineTo(38, 22); c.lineTo(38, 35); c.arc(35.5, 35, 2.5, 0, Math.PI); c.lineTo(33, 26); c.stroke()
        _poly(c, [5, 42, 31, 42], false); c.stroke(); break
    case "car":
        c.beginPath(); c.moveTo(6, 30); c.lineTo(9, 20); c.lineTo(14, 11); c.lineTo(34, 11); c.lineTo(39, 20); c.lineTo(42, 30)
        c.lineTo(42, 38); c.lineTo(6, 38); c.closePath(); c.fill()
        c.fillStyle = knock; _poly(c, [14, 20, 17, 15, 31, 15, 34, 20], true); c.fill()
        _circle(c, 14, 29, 2.8); c.fill(); _circle(c, 34, 29, 2.8); c.fill(); break
    case "defrostF":
        c.beginPath(); c.moveTo(5, 32); c.lineTo(11, 9); c.lineTo(37, 9); c.lineTo(43, 32); c.stroke()
        _zig(c, 16, 17, 40); _zig(c, 24, 17, 40); _zig(c, 32, 17, 40); break
    case "defrostR":
        _rrect(c, 5, 8, 38, 32, 6); c.stroke()
        _zig(c, 16, 15, 34); _zig(c, 24, 15, 34); _zig(c, 32, 15, 34); break
    case "seatFan":
        _seat(c, ink); _fan(c, 11, 36, 9, ink, knock); break
    case "seatVent":
        _seat(c, ink); _fan(c, 38, 12, 9, ink, knock); break
    case "fan":
        _fan(c, 24, 24, 21, ink, knock); break
    case "airflow":
        _seat(c, ink); _zig(c, 8, 4, 22); _zig(c, 16, 4, 22); break
    case "recirc":
        c.beginPath(); c.moveTo(6, 36); c.lineTo(6, 28); c.lineTo(10, 20); c.lineTo(18, 14); c.lineTo(30, 14)
        c.lineTo(38, 20); c.lineTo(42, 28); c.lineTo(42, 36); c.closePath(); c.stroke()
        c.beginPath(); c.arc(24, 27, 6, 0.6, 5.4); c.stroke(); _poly(c, [27, 18, 31, 22, 25, 23], false); c.stroke(); break
    case "music":
        _circle(c, 16, 35, 7); c.fill(); c.fillRect(21, 8, 4, 28)
        c.beginPath(); c.moveTo(25, 8); c.quadraticCurveTo(38, 11, 37, 24); c.lineTo(33, 22); c.quadraticCurveTo(33, 16, 25, 14); c.closePath(); c.fill(); break
    case "phone":
        c.lineWidth = 9
        c.beginPath(); c.moveTo(13, 11); c.bezierCurveTo(6, 17, 15, 36, 36, 40); c.stroke()
        c.lineWidth = _lw; _rrect(c, 7, 7, 11, 11, 4); c.fill(); _rrect(c, 31, 33, 11, 11, 4); c.fill(); break
    case "navi":
        _poly(c, [6, 25, 42, 7, 31, 42, 24, 28], true); c.fill(); break
    case "apps":
        _circle(c, 14, 14, 8); c.fill(); _circle(c, 34, 14, 8); c.fill(); _circle(c, 14, 34, 8); c.fill(); _circle(c, 34, 34, 8); c.fill(); break
    case "manual":
        _poly(c, [24, 12, 7, 8, 7, 36, 24, 41], true); c.stroke(); _poly(c, [24, 12, 41, 8, 41, 36, 24, 41], true); c.stroke(); break
    case "radio":
        _circle(c, 24, 22, 4); c.fill(); _poly(c, [24, 26, 24, 42], false); c.stroke()
        c.beginPath(); c.arc(24, 22, 11, 2.4, 3.9); c.stroke(); c.beginPath(); c.arc(24, 22, 11, -0.75, 0.75); c.stroke()
        c.beginPath(); c.arc(24, 22, 18, 2.4, 3.9); c.stroke(); c.beginPath(); c.arc(24, 22, 18, -0.75, 0.75); c.stroke(); break
    case "home":
        _poly(c, [24, 5, 43, 23, 37, 23, 37, 42, 28, 42, 28, 30, 20, 30, 20, 42, 11, 42, 11, 23, 5, 23], true); c.fill(); break
    case "building":
        _rrect(c, 11, 5, 26, 38, 3); c.fill(); c.fillStyle = knock
        for (i = 0; i < 6; i++) c.fillRect(16 + (i % 2) * 10, 11 + Math.floor(i / 2) * 9, 6, 5)
        c.fillRect(21, 36, 6, 7); break
    case "star":
        c.beginPath()
        for (i = 0; i < 10; i++) {
            var rad = i % 2 === 0 ? 20 : 8.5, ang = -Math.PI / 2 + i * Math.PI / 5
            if (i === 0) c.moveTo(24 + rad * Math.cos(ang), 25 + rad * Math.sin(ang)); else c.lineTo(24 + rad * Math.cos(ang), 25 + rad * Math.sin(ang))
        }
        c.closePath(); c.fill(); break
    case "gear":
        c.beginPath()
        for (i = 0; i < 16; i++) {
            var r2 = i % 2 === 0 ? 21 : 15.5, a2 = i * Math.PI / 8
            if (i === 0) c.moveTo(24 + r2, 24); else c.lineTo(24 + r2 * Math.cos(a2), 24 + r2 * Math.sin(a2))
        }
        c.closePath(); c.fill(); c.fillStyle = knock; _circle(c, 24, 24, 6.5); c.fill(); break
    case "traffic":
        _rrect(c, 12, 3, 24, 42, 8); c.fill(); c.fillStyle = knock
        _circle(c, 24, 14, 4.5); c.fill(); _circle(c, 24, 24, 4.5); c.fill(); _circle(c, 24, 34, 4.5); c.fill(); break
    case "mapfold":
        _poly(c, [6, 12, 18, 6, 30, 12, 42, 6, 42, 36, 30, 42, 18, 36, 6, 42], true); c.stroke()
        _poly(c, [18, 6, 18, 36], false); c.stroke(); _poly(c, [30, 12, 30, 42], false); c.stroke(); break
    case "lane":
        c.beginPath(); c.arc(24, 24, 13, 0.7, 5.5); c.stroke(); _poly(c, [27, 6, 36, 12, 25, 17], false); c.stroke(); break
    case "lock":
        _rrect(c, 8, 20, 32, 22, 4); c.fill(); c.beginPath(); c.arc(24, 20, 8, Math.PI, 0); c.stroke(); break
    case "chevL": _poly(c, [30, 8, 14, 24, 30, 40], false); c.stroke(); break
    case "chevR": _poly(c, [18, 8, 34, 24, 18, 40], false); c.stroke(); break
    case "chevUp": _poly(c, [6, 32, 24, 16, 42, 32], false); c.stroke(); break
    case "arrowUp": _poly(c, [24, 5, 40, 40, 24, 31, 8, 40], true); c.fill(); break
    case "prev":
        c.fillRect(8, 9, 5, 30); _poly(c, [41, 9, 41, 39, 15, 24], true); c.fill(); break
    case "next":
        c.fillRect(35, 9, 5, 30); _poly(c, [7, 9, 7, 39, 33, 24], true); c.fill(); break
    case "play": _poly(c, [13, 6, 13, 42, 42, 24], true); c.fill(); break
    case "pause": _rrect(c, 9, 6, 11, 36, 3); c.fill(); _rrect(c, 28, 6, 11, 36, 3); c.fill(); break
    }
}
