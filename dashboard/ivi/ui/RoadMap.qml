import QtQuick 2.15
import "."
import "geo.js" as Geo

// Dark map: the road network from map.json, the route and the vehicle marker.
//
// Cost model (the board's CPU is what is under test, so nothing here may repaint continuously):
//  * the roads are rasterised ONCE into a north-up off-screen bitmap that is only repainted when the
//    vehicle drives far enough to leave the bitmap's safety margin (or the map/size changes);
//    moving and rotating the map is a scene-graph transform of that bitmap, never a repaint;
//  * the route overlay (a few strokes) repaints at most every 200 ms and only if position or heading changed;
//  * the marker, corners and street-name pill are static items.
Item {
    id: root

    property var mapData: null                  // parsed map.json: {roads:[{c, p, n?}], route:{p}, attribution}
    property var nav: ({})                      // parsed nav.json {x, y, heading, distance, ...}
    property bool headingUp: true               // true = AUTO (heading up), false = north-up
    property real metersAcross: 650             // visible width in metres
    readonly property var info: _info           // Geo.routeInfo: {along, remaining, turn:{distance, angle}|null} or null
    readonly property string streetName: _street

    readonly property real s: width > 0 ? width / metersAcross : 1              // pixels per metre
    readonly property real wk: Math.max(0.6, Math.min(1.2, s / 2.6))            // road width scale
    readonly property real ax: width / 2                                        // vehicle position on screen
    readonly property real ay: height * 0.70
    readonly property real halfDiag: Math.hypot(Math.max(ax, width - ax), Math.max(ay, height - ay))
    readonly property int layerPx: Math.min(4096, Math.ceil(2 * (halfDiag + 280)))
    readonly property real layerMargin: Math.max(40, layerPx / 2 - halfDiag - 8)

    property real fx: 0                         // frame snapshot (taken by the 200 ms tick)
    property real fy: 0
    property real fh: 0
    property var _info: null
    property string _street: ""
    property real layerCx: 0                    // world centre of the bitmap currently displayed
    property real layerCy: 0
    property bool layerReady: false
    property bool _dirty: true
    property var _classes: ({})
    property var _route: null
    property var _named: []

    readonly property real _ox: layerPx / 2 + (fx - layerCx) * s                // vehicle position inside the bitmap
    readonly property real _oy: layerPx / 2 - (fy - layerCy) * s

    onMapDataChanged: { prepare(); layerReady = false; roadLayer.requestPaint(); snapshot() }
    onNavChanged: _dirty = true
    onHeadingUpChanged: _dirty = true
    onSChanged: { layerReady = false; roadLayer.requestPaint() }
    onWidthChanged: _dirty = true
    onHeightChanged: _dirty = true

    function num(v) { return typeof v === "number" && isFinite(v) ? v : 0 }

    function prepare() {
        var groups = {}, d = mapData
        _route = null
        if (d && d.roads) {
            for (var i = 0; i < d.roads.length; i++) {
                var r = d.roads[i], p = r.p
                if (!p || p.length < 4) continue
                var x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9
                for (var j = 0; j < p.length; j += 2) {
                    if (p[j] < x0) x0 = p[j]
                    if (p[j] > x1) x1 = p[j]
                    if (p[j + 1] < y0) y0 = p[j + 1]
                    if (p[j + 1] > y1) y1 = p[j + 1]
                }
                var c = r.c || "residential"
                if (!groups[c]) groups[c] = []
                groups[c].push({ p: p, x0: x0, y0: y0, x1: x1, y1: y1 })
            }
            _named = Geo.namedRoads(d.roads)       // optional road names: only drawn if the data has them
            if (d.route && d.route.p) _route = Geo.buildRoute(d.route.p)
        }
        _classes = groups
    }

    function snapshot() {
        var n = nav || {}
        fx = num(n.x); fy = num(n.y); fh = num(n.heading)
        _info = typeof n.x === "number" || typeof n.distance === "number" ? Geo.routeInfo(_route, n) : null
        _street = _named.length ? Geo.nearestName(_named, fx, fy, 25) : ""
        if (layerReady && Math.hypot(fx - layerCx, fy - layerCy) * s > layerMargin) roadLayer.requestPaint()
        overlay.requestPaint()
    }

    Timer {
        interval: 200; repeat: true; running: root.visible && root.width > 0
        onTriggered: if (root._dirty) { root._dirty = false; root.snapshot() }
    }

    Item {
        anchors.fill: parent
        clip: true
        Rectangle { anchors.fill: parent; color: Theme.surface }

        Canvas {   // north-up road bitmap, moved/rotated by transform only
            id: roadLayer
            visible: root.layerReady
            width: root.layerPx; height: root.layerPx
            x: root.ax - root._ox
            y: root.ay - root._oy
            renderTarget: Canvas.Image
            renderStrategy: Canvas.Threaded
            transform: Rotation { origin.x: root._ox; origin.y: root._oy; angle: root.headingUp ? -root.fh : 0 }

            property real pendingCx: 0
            property real pendingCy: 0
            // grey ramp by road class: major roads lighter and wider; [colour, width px, dark casing]
            readonly property var styles: ({
                motorway:    ["#7a7d8c", 20, true], trunk:          ["#7a7d8c", 20, true],
                primary:     ["#6a6d7b", 16, true], primary_link:   ["#6a6d7b", 9, true],
                secondary:   ["#585b69", 12, true], secondary_link: ["#585b69", 8, true],
                tertiary:    ["#464954", 8, false], tertiary_link:  ["#464954", 6, false],
                residential: ["#383a44", 6, false], unclassified:   ["#383a44", 6, false],
                service:     ["#2c2e36", 4, false] })
            readonly property var order: ["service", "residential", "unclassified", "tertiary_link", "tertiary",
                "secondary_link", "secondary", "primary_link", "primary", "trunk", "motorway"]

            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var s = root.s, cx = root.fx, cy = root.fy
                pendingCx = cx; pendingCy = cy
                if (!(s > 0)) return
                var half = width / 2 / s + 30
                ctx.translate(width / 2, height / 2)
                ctx.scale(s, -s)
                ctx.translate(-cx, -cy)
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                for (var o = 0; o < order.length; o++) {
                    var list = root._classes[order[o]]
                    if (!list) continue
                    var st = styles[order[o]], vis = []
                    for (var i = 0; i < list.length; i++) {
                        var r = list[i]
                        if (r.x1 < cx - half || r.x0 > cx + half || r.y1 < cy - half || r.y0 > cy + half) continue
                        vis.push(r.p)
                    }
                    if (!vis.length) continue
                    var w = st[1] * root.wk
                    for (var pass = st[2] ? 0 : 1; pass < 2; pass++) {
                        ctx.beginPath()
                        for (var v = 0; v < vis.length; v++) {
                            var p = vis[v]
                            ctx.moveTo(p[0], p[1])
                            for (var q = 2; q < p.length; q += 2) ctx.lineTo(p[q], p[q + 1])
                        }
                        ctx.strokeStyle = pass === 0 ? Theme.bg.toString() : st[0]
                        ctx.lineWidth = (pass === 0 ? w + 4 * root.wk : w) / s
                        ctx.stroke()
                    }
                }
            }
            onPainted: { root.layerCx = pendingCx; root.layerCy = pendingCy; root.layerReady = true }
        }

        Canvas {   // route overlay: travelled part dim, remaining part bright
            id: overlay
            anchors.fill: parent
            renderTarget: Canvas.Image

            function strokeRange(ctx, rt, a, b) {   // route distance range [a, b] (metres) as one polyline
                if (b <= a) return
                var pt = Geo.pointAt(rt, a)
                ctx.beginPath()
                ctx.moveTo(pt[0], pt[1])
                var i = Geo.indexAt(rt.cum, a) + 1
                while (i < rt.n && rt.cum[i] < b) { ctx.lineTo(rt.p[2 * i], rt.p[2 * i + 1]); i++ }
                pt = Geo.pointAt(rt, b)
                ctx.lineTo(pt[0], pt[1])
                ctx.stroke()
            }

            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var rt = root._route, inf = root._info
                if (!rt || !inf) return
                var s = root.s, R = root.halfDiag / s + 40, along = inf.along
                ctx.translate(root.ax, root.ay)
                ctx.rotate(root.headingUp ? -root.fh * Math.PI / 180 : 0)
                ctx.scale(s, -s)
                ctx.translate(-root.fx, -root.fy)
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                var core = 9 * root.wk / s
                ctx.lineWidth = core
                ctx.strokeStyle = Theme.accentDim.toString()
                strokeRange(ctx, rt, Math.max(0, along - R), along)
                ctx.strokeStyle = Theme.accent.toString()
                strokeRange(ctx, rt, along, Math.min(rt.length, along + R))
                if (rt.closed && along + R > rt.length) strokeRange(ctx, rt, 0, along + R - rt.length)
            }
        }

        Rectangle {   // street name: only when the map data really carries one
            visible: root.streetName !== ""
            x: root.ax - width / 2; y: root.ay + 54
            width: nameText.implicitWidth + 2 * Theme.s3; height: 48; radius: height / 2; color: Theme.overlay
            Text { id: nameText; anchors.centerIn: parent; text: root.streetName; color: Theme.ink
                font.pixelSize: Theme.tXs; font.family: Theme.fBody }
        }

        Item {   // vehicle marker: white-ringed blue disc with an arrow (turns in north-up mode)
            x: root.ax; y: root.ay
            rotation: root.headingUp ? 0 : root.fh
            Rectangle { width: 72; height: 72; radius: 36; x: -36; y: -36; color: Theme.ink }
            Rectangle { width: 60; height: 60; radius: 30; x: -30; y: -30; color: Theme.accent }
            Icon { x: -16; y: -16; kind: "arrowUp"; size: 32; knock: Theme.accent }
        }
    }

    Repeater {   // rounded corners, painted once
        model: [{ r: 0, ax: true, ay: true }, { r: 90, ax: false, ay: true }, { r: 180, ax: false, ay: false }, { r: 270, ax: true, ay: false }]
        Canvas {
            width: Theme.rPanel; height: Theme.rPanel
            x: modelData.ax ? 0 : root.width - width
            y: modelData.ay ? 0 : root.height - height
            rotation: modelData.r
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.fillStyle = Theme.bg.toString()
                ctx.beginPath()
                ctx.moveTo(0, 0); ctx.lineTo(width, 0)
                ctx.arc(width, height, width, -Math.PI / 2, Math.PI, true)
                ctx.closePath(); ctx.fill()
            }
        }
    }
}
