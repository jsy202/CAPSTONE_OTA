import QtQuick 2.15
import QtQuick.Window 2.15

// Vector icon family drawn on a 48x48 grid with a QtQuick Canvas.
// Usage: Icon { name: "search"; size: 48; color: "white"; accent: "#44464e" }
//  - color  : main glyph colour
//  - accent : secondary colour (profile disc, dimmed fan of the seat icons); empty = per-icon default
// Strokes are 3.5 units wide with round caps/joins; filled glyphs get a thin
// round-joined outline so corners stay soft like the reference.
Item {
    id: root

    property string name: ""
    property real size: 48
    property color color: "white"
    property color accent: "transparent"
    property bool hasAccent: false   // set true to force `accent` even when it is transparent

    readonly property var names: [
        "search", "search-small", "bell", "lte", "volume", "bluetooth", "profile", "car",
        "seat-heat-left", "seat-heat-right", "defrost-front", "defrost-rear", "fan", "fan-speed",
        "air-direction", "recirculation", "media", "phone", "navigation", "apps", "manual",
        "radio", "home", "building", "star", "fuel", "parking", "compass-2d", "map-auto",
        "traffic-light", "settings", "chevron-left", "chevron-right", "chevron-up", "lock-car",
        "voice"
    ]

    width: size
    height: size

    onNameChanged: canvas.requestPaint()
    onColorChanged: canvas.requestPaint()
    onAccentChanged: canvas.requestPaint()
    onSizeChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        renderTarget: Canvas.Image
        // Backing store follows the device pixel ratio so edges stay crisp when scaled.
        canvasSize: Qt.size(root.size * Screen.devicePixelRatio, root.size * Screen.devicePixelRatio)

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var k = canvas.canvasSize.width / 48
            ctx.setTransform(k, 0, 0, k, 0, 0)
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            var accent = root.hasAccent || root.accent.a > 0 ? root.accent : "#4a4b4e"
            if (root.name === "profile" && !(root.hasAccent || root.accent.a > 0))
                accent = "#44464e"
            var h = makeHelper(ctx, root.color, accent)
            var fn = glyphs[root.name]
            if (fn)
                fn(h)
            else
                console.warn("Icon: unknown name '" + root.name + "'")
        }
    }

    // ---- drawing helpers --------------------------------------------------

    // Minimal SVG path interpreter: absolute M L H V C Q A Z (circular arcs only).
    function tracePath(ctx, d) {
        var t = d.match(/[MLHVCQAZ]|-?\d*\.?\d+/g)
        var i = 0, cx = 0, cy = 0, sx = 0, sy = 0, cmd = ""
        function n() { return parseFloat(t[i++]) }
        ctx.beginPath()
        while (i < t.length) {
            if (/[A-Z]/.test(t[i]))
                cmd = t[i++]
            if (cmd === "Z") {
                ctx.closePath(); cx = sx; cy = sy; continue
            }
            if (cmd === "M") {
                cx = n(); cy = n(); sx = cx; sy = cy; ctx.moveTo(cx, cy); cmd = "L"
            } else if (cmd === "L") {
                cx = n(); cy = n(); ctx.lineTo(cx, cy)
            } else if (cmd === "H") {
                cx = n(); ctx.lineTo(cx, cy)
            } else if (cmd === "V") {
                cy = n(); ctx.lineTo(cx, cy)
            } else if (cmd === "C") {
                var a = n(), b = n(), c = n(), e = n(), x = n(), y = n()
                ctx.bezierCurveTo(a, b, c, e, x, y); cx = x; cy = y
            } else if (cmd === "Q") {
                var qa = n(), qb = n(), qx = n(), qy = n()
                ctx.quadraticCurveTo(qa, qb, qx, qy); cx = qx; cy = qy
            } else if (cmd === "A") {
                var r = n(); n(); n(); var fa = n(), fs = n(), ex = n(), ey = n()
                var dx = (cx - ex) / 2, dy = (cy - ey) / 2, d2 = dx * dx + dy * dy
                if (r * r < d2) r = Math.sqrt(d2)
                var kk = Math.sqrt(Math.max(0, (r * r - d2) / d2)) * (fa !== fs ? 1 : -1)
                var ox = kk * dy + (cx + ex) / 2, oy = -kk * dx + (cy + ey) / 2
                ctx.arc(ox, oy, r, Math.atan2(cy - oy, cx - ox), Math.atan2(ey - oy, ex - ox), !fs)
                cx = ex; cy = ey
            } else {
                break
            }
        }
    }

    function makeHelper(ctx, color, accent) {
        var cur = color
        var h = {
            ctx: ctx,
            useAccent: function (on) { cur = on ? accent : color },
            // stroked path
            S: function (d, w) {
                tracePath(ctx, d)
                ctx.strokeStyle = cur; ctx.lineWidth = w || 3.5; ctx.stroke()
            },
            // filled path with a soft round-joined outline
            F: function (d, soft) {
                tracePath(ctx, d)
                ctx.fillStyle = cur; ctx.fill()
                ctx.strokeStyle = cur; ctx.lineWidth = soft === undefined ? 2 : soft; ctx.stroke()
            },
            // circle: mode "f" fill, "s" stroke
            C: function (x, y, r, mode, w) {
                ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2)
                if (mode === "s") { ctx.strokeStyle = cur; ctx.lineWidth = w || 3.5; ctx.stroke() }
                else { ctx.fillStyle = cur; ctx.fill() }
            },
            // rounded rectangle
            R: function (x, y, w, hh, r, mode, lw) {
                var d = "M" + (x + r) + " " + y + " H" + (x + w - r) + " A" + r + " " + r + " 0 0 1 " + (x + w) + " " + (y + r)
                    + " V" + (y + hh - r) + " A" + r + " " + r + " 0 0 1 " + (x + w - r) + " " + (y + hh)
                    + " H" + (x + r) + " A" + r + " " + r + " 0 0 1 " + x + " " + (y + hh - r)
                    + " V" + (y + r) + " A" + r + " " + r + " 0 0 1 " + (x + r) + " " + y + " Z"
                if (mode === "s") h.S(d, lw)
                else h.F(d, 0.01)
            },
            // erase (cut-out) helpers: punch transparent holes into what is drawn so far
            KS: function (d, w) {
                ctx.save(); ctx.globalCompositeOperation = "destination-out"
                tracePath(ctx, d); ctx.strokeStyle = "black"; ctx.lineWidth = w; ctx.stroke(); ctx.restore()
            },
            KF: function (d) {
                ctx.save(); ctx.globalCompositeOperation = "destination-out"
                tracePath(ctx, d); ctx.fillStyle = "black"; ctx.fill(); ctx.restore()
            },
            // 4-blade pinwheel fan centred at (x, y), radius ~ s
            fan: function (x, y, s) {
                for (var i = 0; i < 4; i++) {
                    ctx.save(); ctx.translate(x, y); ctx.rotate(i * Math.PI / 2 + 0.35); ctx.scale(s / 20, s / 20)
                    ctx.beginPath()
                    ctx.moveTo(2, -3)
                    ctx.bezierCurveTo(-0.5, -9, 1, -18.5, 8.5, -18.5)
                    ctx.bezierCurveTo(15.5, -18.5, 17, -10, 11, -5)
                    ctx.bezierCurveTo(8, -2.5, 4.5, -2, 2, -3)
                    ctx.closePath(); ctx.fillStyle = cur; ctx.fill()
                    ctx.restore()
                }
            },
            // filled triangle
            tri: function (x1, y1, x2, y2, x3, y3) {
                h.F("M" + x1 + " " + y1 + " L" + x2 + " " + y2 + " L" + x3 + " " + y3 + " Z", 1.2)
            },
            // wavy up-arrow (defrost): x = centre, y0 = bottom, y1 = arrow tip
            wave: function (x, y0, y1) {
                var m = (y0 + y1) / 2
                h.S("M" + x + " " + y0 + " C" + (x - 4) + " " + (y0 - 4) + " " + (x + 4) + " " + (m + 3) + " " + x + " " + (m - 1)
                    + " V" + (y1 + 4), 3)
                h.tri(x, y1 - 1, x - 4.2, y1 + 7, x + 4.2, y1 + 7)
            },
            seat: function () {
                // seat facing left: back on the right, cushion extends left
                h.S("M39 8 L38 29", 6.5)
                h.F("M38 29 L38 40 H14 C11.5 40 10 38.5 10 36.5 C10 32.5 13 30.5 17 30.5 Z", 2)
            }
        }
        return h
    }

    // ---- glyph table (48x48 grid) ------------------------------------------

    readonly property var glyphs: ({
        "search": function (h) {
            h.C(21, 21, 12.5, "s", 4)
            h.S("M30.5 30.5 L41.5 41.5", 4.5)
        },
        "search-small": function (h) { glyphs["search"](h) },
        "bell": function (h) {
            h.F("M24 5 C16.5 5 13 11 13 18.5 V26 C13 28.5 11.5 30.5 9.5 32.5 V35 H38.5 V32.5 C36.5 30.5 35 28.5 35 26 V18.5 C35 11 31.5 5 24 5 Z", 2)
            h.F("M19.5 39 H28.5 C28.5 42 26.5 44 24 44 C21.5 44 19.5 42 19.5 39 Z", 1.5)
        },
        "lte": function (h) {
            // letters L T E drawn as strokes, then four signal bars
            h.S("M3 6 V14 H8.5", 2.4)
            h.S("M11 6.5 H19 M15 6.5 V14", 2.4)
            h.S("M28 6.5 H22.5 V14 H28 M22.5 10.3 H26.8", 2.4)
            h.S("M7 42 V41", 4.2)
            h.S("M17 42 V32", 4.2)
            h.S("M27 42 V22", 4.2)
            h.S("M37 42 V8", 4.2)
        },
        "volume": function (h) {
            h.F("M4 19 H11 L21 10 C22 9.2 23.5 9.8 23.5 11 V37 C23.5 38.2 22 38.8 21 38 L11 29 H4 Z", 2.5)
            h.S("M29 17.5 C31.8 21.5 31.8 26.5 29 30.5", 3.2)
            h.S("M34.5 12.5 C39.5 19 39.5 29 34.5 35.5", 3.2)
        },
        "bluetooth": function (h) {
            h.S("M12 16 L34 33 L24 42 V6 L34 15 L12 32", 3.8)
        },
        "profile": function (h) {
            h.useAccent(true)
            h.C(24, 24, 23.5, "f")
            h.useAccent(false)
            h.C(24, 18.5, 6.5, "f")
            h.F("M11.5 37 C11.5 31 16.5 27.5 24 27.5 C31.5 27.5 36.5 31 36.5 37 C36.5 38 36 38.5 35 38.5 H13 C12 38.5 11.5 38 11.5 37 Z", 1.5)
        },
        "car": function (h) {
            h.F("M14.5 17 L18 8.5 C18.4 7.7 19.2 7.2 20 7.2 H28 C28.8 7.2 29.6 7.7 30 8.5 L33.5 17 Z", 1.5)
            h.S("M4.5 19.5 H43.5", 3)
            h.F("M8.5 21.5 H39.5 V37 C39.5 38 39 38.5 38 38.5 H34.5 C33.5 38.5 33 38 33 37 V35 H15 V37 C15 38 14.5 38.5 13.5 38.5 H10 C9 38.5 8.5 38 8.5 37 Z", 2)
            h.KS("M14 28 H34", 3.4)
        },
        "seat-heat-left": function (h) {
            h.seat()
            h.useAccent(true)
            h.fan(21, 15, 13)
            h.useAccent(false)
            h.tri(2.5, 20, 8.5, 16, 8.5, 24)
        },
        "seat-heat-right": function (h) {
            var ctx = h.ctx
            ctx.save(); ctx.translate(48, 0); ctx.scale(-1, 1)
            glyphs["seat-heat-left"](h)
            ctx.restore()
        },
        "defrost-front": function (h) {
            h.S("M5.5 14 C14 5.5 34 5.5 42.5 14 L38.5 31 C31 36 17 36 9.5 31 Z", 3.4)
            h.wave(16.5, 41, 14)
            h.wave(24, 41, 14)
            h.wave(31.5, 41, 14)
        },
        "defrost-rear": function (h) {
            h.R(5, 7, 38, 30, 5, "s", 3.4)
            h.wave(16.5, 42, 15)
            h.wave(24, 42, 15)
            h.wave(31.5, 42, 15)
        },
        "fan": function (h) { h.fan(24, 24, 22) },
        "fan-speed": function (h) { h.fan(24, 24, 22) },
        "air-direction": function (h) {
            h.C(39.5, 9.5, 4.8, "f")
            h.S("M37 17 L28 30", 5.5)
            h.S("M28 30 L19.5 41", 4.5)
            h.S("M36 19 L31 20 L24 29", 3)
            h.S("M12 17 H27", 3)
            h.tri(28.5, 17, 23.5, 13.2, 23.5, 20.8)
            h.tri(5, 17, 10, 13.2, 10, 20.8)
            h.S("M14.5 21 L16 27", 2.6)
            h.tri(16.8, 32, 13, 26, 19.6, 25)
        },
        "recirculation": function (h) {
            h.S("M41 8.5 H31 L22.5 17.5 L10 20.5 C7 21.3 5.5 23.3 5.5 26.5 V34 H11", 3.4)
            h.S("M23 34 H41", 3.4)
            h.C(17, 37.5, 5, "s", 3.2)
            h.S("M41 21 H32.5 A4.6 4.6 0 0 0 32.5 30.2 H28.5", 3)
            h.S("M32 26 L27.8 30.2 L32 34.4", 3)
        },
        "media": function (h) {
            h.C(27.5, 35.5, 6.2, "f")
            h.S("M33.7 35 V9.5 C34 17.5 41.5 18 41.5 27", 4.6)
            // small bluetooth rune on the left
            var ctx = h.ctx
            ctx.save(); ctx.translate(2.5, 7); ctx.scale(0.5, 0.5)
            h.S("M12 16 L34 33 L24 42 V6 L34 15 L12 32", 4.6)
            ctx.restore()
        },
        "phone": function (h) {
            h.S("M14.5 8 C11 9.5 9 12 10.5 17.5 C13.5 28.5 21 36 31 38.5 C36 39.5 38.5 37 38.5 34 L37.5 32.5 L31 29 L27.5 32 C23.5 30 20 26.5 18 22 L21 18.5 L17.5 11.5 Z", 3.8)
        },
        "navigation": function (h) {
            h.F("M40.5 7.5 L27 40.5 C26.6 41.5 25.2 41.4 25 40.4 L22.5 26 L8 23.5 C7 23.3 6.8 21.9 7.8 21.5 Z", 3)
        },
        "apps": function (h) {
            h.R(6, 6, 16.5, 16.5, 7, "f")
            h.R(25.5, 6, 16.5, 16.5, 7, "f")
            h.R(6, 25.5, 16.5, 16.5, 7, "f")
            h.R(25.5, 25.5, 16.5, 16.5, 7, "f")
        },
        "manual": function (h) {
            h.F("M23 11.5 L7 7.5 V33 L23 37 Z", 1.5)
            h.F("M25 11.5 L41 7.5 V33 L25 37 Z", 1.5)
            h.S("M5.5 25 V41 H42.5 V25", 3.4)
            h.KS("M24 6 V40", 1.2)
        },
        "radio": function (h) {
            h.C(24, 17, 3.6, "f")
            h.S("M24 20 V41", 3.8)
            h.S("M18.9 10.9 A8 8 0 0 0 18.9 23.1", 3.2)
            h.S("M29.1 10.9 A8 8 0 0 1 29.1 23.1", 3.2)
            h.S("M14.2 5.5 A15 15 0 0 0 14.2 28.5", 3.2)
            h.S("M33.8 5.5 A15 15 0 0 1 33.8 28.5", 3.2)
        },
        "home": function (h) {
            h.F("M24 5.5 L4.5 23 H10.5 V41.5 H20 V30 H28 V41.5 H37.5 V23 H43.5 Z", 3)
        },
        "building": function (h) {
            h.F("M10 6 H30 V42 H10 Z", 2.5)
            h.F("M31 18 H39 V42 H31 Z", 2.5)
            var ys = [11, 18, 25, 32]
            for (var i = 0; i < ys.length; i++) {
                h.KF("M14.5 " + ys[i] + " H18 V" + (ys[i] + 3.5) + " H14.5 Z")
                h.KF("M22 " + ys[i] + " H25.5 V" + (ys[i] + 3.5) + " H22 Z")
            }
            h.KF("M34 23 H36.5 V26 H34 Z")
            h.KF("M34 30 H36.5 V33 H34 Z")
            h.KF("M17.5 37 H22.5 V42 H17.5 Z")
        },
        "star": function (h) {
            var d = ""
            for (var i = 0; i < 10; i++) {
                var r = i % 2 === 0 ? 20 : 8.8
                var a = -Math.PI / 2 + i * Math.PI / 5
                d += (i === 0 ? "M" : " L") + (24 + r * Math.cos(a)).toFixed(2) + " " + (25 + r * Math.sin(a)).toFixed(2)
            }
            h.F(d + " Z", 3.5)
        },
        "fuel": function (h) {
            h.F("M12 8 C12 6.3 13.3 5 15 5 H27 C28.7 5 30 6.3 30 8 V41 H12 Z", 1.5)
            h.KF("M16.5 9.5 H25.5 V19 H16.5 Z")
            h.S("M8 41.5 H34", 3.4)
            h.S("M30 17 H33.5 C35.5 17 37 18.5 37 20.5 V32 C37 35.5 42 35.5 42 32 V15.5 L37.5 10", 3.2)
            h.tri(1.5, 17.5, 7.5, 13.5, 7.5, 21.5)
        },
        "parking": function (h) {
            var ctx = h.ctx
            ctx.lineCap = "butt"
            ctx.lineJoin = "miter"
            h.S("M15 6 V43", 7.5)
            ctx.lineJoin = "round"
            h.S("M15 9.5 H26 C32.5 9.5 36.5 13 36.5 19 C36.5 25 32.5 28.5 26 28.5 H15", 7)
        },
        "compass-2d": function (h) {
            h.F("M24 4.5 L38.5 41 C39 42.2 37.8 43.3 36.7 42.7 L24 35 L11.3 42.7 C10.2 43.3 9 42.2 9.5 41 Z", 3)
        },
        "map-auto": function (h) {
            h.S("M5.5 12 L17 8 L31 12 L42.5 8 V36 L31 40 L17 36 L5.5 40 Z", 3.4)
            h.S("M17 8.5 V36 M31 12 V39.5", 3)
        },
        "traffic-light": function (h) {
            h.R(13, 4, 22, 40, 8, "s", 3.4)
            h.C(24, 14, 3.6, "f")
            h.C(24, 24, 3.6, "f")
            h.C(24, 34, 3.6, "f")
        },
        "settings": function (h) {
            var d = "", pts = []
            for (var i = 0; i < 8; i++) {
                var a = i * Math.PI / 4
                var seq = [[-17, 15.6], [-9.5, 21], [9.5, 21], [17, 15.6]]
                for (var j = 0; j < 4; j++) {
                    var aa = a + seq[j][0] * Math.PI / 180
                    pts.push((24 + seq[j][1] * Math.cos(aa)).toFixed(2) + " " + (24 + seq[j][1] * Math.sin(aa)).toFixed(2))
                }
            }
            d = "M" + pts.join(" L") + " Z"
            h.F(d, 2.5)
            h.ctx.save(); h.ctx.globalCompositeOperation = "destination-out"
            h.ctx.beginPath(); h.ctx.arc(24, 24, 7, 0, Math.PI * 2); h.ctx.fillStyle = "black"; h.ctx.fill()
            h.ctx.restore()
        },
        "chevron-left": function (h) { h.S("M31 8 L15 24 L31 40", 4) },
        "chevron-right": function (h) { h.S("M17 8 L33 24 L17 40", 4) },
        "chevron-up": function (h) { h.S("M8 31 L24 15 L40 31", 4) },
        "lock-car": function (h) {
            h.F("M4.5 33 V25 C4.5 22.8 6 21.4 8 20.8 L17 18.5 L23 11 C23.8 10 25 9.5 26.2 9.5 H33 C34.6 9.5 36 10.8 36 12.4 V33 Z", 2)
            h.KF("M29 24 H46 V44 H29 Z")
            h.F("M31.5 29.5 H43 V40 H31.5 Z", 2)
            h.S("M34 29 V26.5 C34 23.5 40.5 23.5 40.5 26.5 V29", 2.6)
            h.KF("M36.3 33.5 H37.7 V37 H36.3 Z")
        },
        "voice": function (h) {
            h.S("M13 8.5 H35 C39.5 8.5 42.5 11.5 42.5 16 V24 C42.5 28.5 39.5 31.5 35 31.5 H24 L15 38.5 V31.5 H13 C8.5 31.5 5.5 28.5 5.5 24 V16 C5.5 11.5 8.5 8.5 13 8.5 Z", 3.4)
            h.S("M33 23.5 A7.5 7.5 0 1 0 25.5 36", 3.2)
            h.S("M30 32.5 L25.5 36.3 L30.5 39.5", 3.2)
        }
    })

    Component.onCompleted: canvas.requestPaint()
}
