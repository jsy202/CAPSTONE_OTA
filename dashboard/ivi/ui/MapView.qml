import QtQuick 2.15
import "geo.js" as Geo

// Dark infotainment map: the real road network from map.json, the route, the vehicle marker,
// map buttons on the right edge and a floating search/route card at the lower left.
//
// Cost model (the board's CPU is what is under test):
//  * the road network is rasterised into ONE north-up off-screen canvas that is only repainted when
//    the vehicle has driven far enough to leave its safety margin (or the map/zoom/size changes);
//    moving and rotating the map is a scene-graph transform of that bitmap, not a repaint;
//  * the route overlay (a few polyline strokes) and the card text are refreshed by a 200 ms tick,
//    so nothing here paints faster than 5 fps.
Item {
    id: root

    // ---- public API ----
    property var mapData: null                  // parsed map.json: {origin, roads:[{c, p, n?}], route:{p, length}, attribution}
    property var nav: ({ x: 0, y: 0, heading: 0, speed: 0, distance: 0 })   // parsed nav.json
    property bool headingUp: true               // true = AUTO (heading points up), false = 2D north-up
    property real metersAcross: 650             // visible width in metres
    property var congestion: []                 // optional [[d0, d1], ...] route ranges in metres, drawn red; no data => none
    property string fontFamily: ""
    property string searchText: "Search"
    property color accent: "#1e9bff"
    property color cornerColor: "#131417"       // colour outside the rounded map corners
    signal toggledHeadingUp()

    // ---- layout scale: the design is ~1700x1180, the live area ~1450x540 ----
    readonly property real k: Math.max(0.75, Math.min(1.0, height / 800))
    readonly property real s: width > 0 ? width / metersAcross : 1             // pixels per metre
    readonly property real wk: Math.max(0.6, Math.min(1.2, s / 2.6))            // road width scale
    readonly property real ax: width / 2                                        // vehicle position on screen
    readonly property real ay: height * 0.70
    readonly property real halfDiag: Math.hypot(Math.max(ax, width - ax), Math.max(ay, height - ay))
    readonly property int layerPx: Math.min(4096, Math.ceil(2 * (halfDiag + 280)))
    readonly property real layerMargin: Math.max(40, layerPx / 2 - halfDiag - 8)   // px the vehicle may drift from the layer centre

    // ---- frame state: everything on screen is drawn from this snapshot, taken by the 200 ms tick ----
    property real fx: 0
    property real fy: 0
    property real fh: 0
    property var info: null                     // Geo.routeInfo result
    property string streetName: ""
    property real pulse: 0
    property real layerCx: 0                    // world centre of the road bitmap that is currently displayed
    property real layerCy: 0
    property bool layerReady: false
    property bool _dirty: true
    property var _classes: ({})
    property var _route: null
    property var _named: []

    readonly property real _ox: layerPx / 2 + (fx - layerCx) * s              // vehicle position inside the bitmap
    readonly property real _oy: layerPx / 2 - (fy - layerCy) * s

    onMapDataChanged: { prepare(); layerReady = false; roadLayer.requestPaint(); snapshot() }
    onNavChanged: _dirty = true
    onHeadingUpChanged: _dirty = true
    onSChanged: { layerReady = false; roadLayer.requestPaint() }
    onWidthChanged: _dirty = true
    onHeightChanged: _dirty = true
    onCongestionChanged: _dirty = true

    function num(v) { return typeof v === "number" && isFinite(v) ? v : 0 }

    function prepare() {
        var groups = {}, named = [], d = mapData
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
            named = Geo.namedRoads(d.roads)
            if (d.route && d.route.p) _route = Geo.buildRoute(d.route.p)
        }
        _classes = groups
        _named = named
    }

    // Copy the live nav into the frame snapshot and decide what needs repainting.
    function snapshot() {
        var n = nav || {}
        fx = num(n.x); fy = num(n.y); fh = num(n.heading)
        info = Geo.routeInfo(_route, n)
        streetName = _named.length ? Geo.nearestName(_named, fx, fy, 25) : ""
        if (layerReady && Math.hypot(fx - layerCx, fy - layerCy) * s > layerMargin) roadLayer.requestPaint()
        overlay.requestPaint()
    }

    Timer {
        interval: 200; repeat: true; running: root.visible && root.width > 0
        onTriggered: {
            root.pulse = (Date.now() % 1600) / 1600
            if (root._dirty) { root._dirty = false; root.snapshot() }
        }
    }

    // ================= map =================
    Item {
        id: viewport
        anchors.fill: parent
        clip: true

        Rectangle { anchors.fill: parent; color: "#111518" }

        // North-up road bitmap, centred on layerCx/layerCy, moved/rotated by transform only.
        Canvas {
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
            readonly property var styles: ({
                motorway:       ["#555967", 20, true],  trunk:          ["#555967", 20, true],
                primary:        ["#4a4d5a", 16, true],  primary_link:   ["#4a4d5a", 9, true],
                secondary:      ["#434652", 12, true],   secondary_link: ["#434652", 8, true],
                tertiary:       ["#3d3f48", 8, false],  tertiary_link:  ["#3d3f48", 6, false],
                residential:    ["#363842", 6, false],  unclassified:   ["#363842", 6, false],
                service:        ["#2e3039", 4, false] })
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
                        // pass 0: dark casing under the major roads, pass 1: the road itself
                        ctx.strokeStyle = pass === 0 ? "#0c0e11" : st[0]
                        ctx.lineWidth = (pass === 0 ? w + 4 * root.wk : w) / s
                        ctx.stroke()
                    }
                }
            }
            onPainted: {
                root.layerCx = pendingCx; root.layerCy = pendingCy
                root.layerReady = true
            }
        }

        // Route overlay: view sized, repainted by the 200 ms tick only.
        Canvas {
            id: overlay
            anchors.fill: parent
            renderTarget: Canvas.Image

            // Strokes route distance range [a, b] (metres) as one polyline.
            function strokeRange(ctx, rt, a, b) {
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
                var rt = root._route, inf = root.info
                if (!rt || !inf) return
                var s = root.s, R = root.halfDiag / s + 40, along = inf.along
                ctx.translate(root.ax, root.ay)
                ctx.rotate(root.headingUp ? -root.fh * Math.PI / 180 : 0)
                ctx.scale(s, -s)
                ctx.translate(-root.fx, -root.fy)
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                var wide = 20 * root.wk / s, core = 9 * root.wk / s
                var accent = root.accent
                // already driven: dim
                ctx.strokeStyle = Qt.rgba(accent.r, accent.g, accent.b, 0.20); ctx.lineWidth = core
                strokeRange(ctx, rt, Math.max(0, along - R), along)
                // still ahead: glow + core (a loop keeps going past its end)
                var ahead = [[along, Math.min(rt.length, along + R)]]
                if (rt.closed && along + R > rt.length) ahead.push([0, along + R - rt.length])
                for (var a = 0; a < ahead.length; a++) {
                    ctx.strokeStyle = Qt.rgba(accent.r, accent.g, accent.b, 0.28); ctx.lineWidth = wide
                    strokeRange(ctx, rt, ahead[a][0], ahead[a][1])
                    ctx.strokeStyle = accent; ctx.lineWidth = core
                    strokeRange(ctx, rt, ahead[a][0], ahead[a][1])
                }
                // congestion ranges, if the data source provides any
                var cg = root.congestion
                if (cg && cg.length) {
                    ctx.strokeStyle = "#e5484d"; ctx.lineWidth = core
                    for (var c = 0; c < cg.length; c++)
                        strokeRange(ctx, rt, Math.max(cg[c][0], along - R), Math.min(cg[c][1], along + R))
                }
            }
        }

        // pill with the current street name -- only when the map data really has one
        Rectangle {
            visible: root.streetName !== ""
            x: root.ax - width / 2; y: root.ay + marker.size / 2 + 14 * root.k
            width: nameText.implicitWidth + 44 * root.k; height: 52 * root.k; radius: height / 2
            color: "#d92a2b31"
            Text { id: nameText; anchors.centerIn: parent; text: root.streetName; color: "#ffffff"
                font.pixelSize: 28 * root.k; font.family: root.fontFamily }
        }

        // vehicle marker: pulsing ring, white-ringed blue disc, arrow
        Item {
            id: marker
            readonly property real size: 80 * root.k
            x: root.ax; y: root.ay
            Rectangle {   // pulse
                width: marker.size * (1 + 0.9 * root.pulse); height: width; radius: width / 2
                x: -width / 2; y: -height / 2
                color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.30 * (1 - root.pulse))
            }
            Rectangle { width: marker.size; height: width; radius: width / 2; x: -width / 2; y: -height / 2; color: "#ffffff" }
            Rectangle { width: marker.size - 12 * root.k; height: width; radius: width / 2; x: -width / 2; y: -height / 2; color: root.accent }
            Glyph {
                kind: "navarrow"; color: "#ffffff"
                width: marker.size * 0.5; height: width; x: -width / 2; y: -height / 2
                rotation: root.headingUp ? 0 : root.fh
            }
        }
    }

    // dark rounded corners
    Repeater {
        model: [{ r: 0, ax: true, ay: true }, { r: 90, ax: false, ay: true }, { r: 180, ax: false, ay: false }, { r: 270, ax: true, ay: false }]
        Canvas {
            readonly property real rad: 30 * root.k
            width: rad; height: rad
            x: modelData.ax ? 0 : root.width - width
            y: modelData.ay ? 0 : root.height - height
            rotation: modelData.r
            onRadChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.fillStyle = root.cornerColor
                ctx.beginPath()
                ctx.moveTo(0, 0); ctx.lineTo(width, 0)
                ctx.arc(width, height, width, -Math.PI / 2, Math.PI, true)
                ctx.closePath(); ctx.fill()
            }
        }
    }

    // ================= buttons, right edge =================
    readonly property real btn: Math.round(80 * k)
    readonly property real edge: Math.round(24 * k)

    MapButton {
        id: btn2d
        width: root.btn; height: root.btn; x: root.width - width - root.edge; y: root.edge
        onClicked: root.toggledHeadingUp()
        Column {
            anchors.centerIn: parent; spacing: 2 * root.k
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "2D"; color: "#ffffff"; font.bold: true
                font.pixelSize: 28 * root.k; font.family: root.fontFamily }
            Glyph { kind: "navarrow"; color: "#ffffff"; width: 24 * root.k; height: width; anchors.horizontalCenter: parent.horizontalCenter
                rotation: root.headingUp ? 0 : root.fh }   // in north-up mode it doubles as a heading indicator
        }
    }
    MapButton {
        id: btnAuto
        width: root.btn; height: root.btn; x: btn2d.x; y: btn2d.y + root.btn + 14 * root.k
        onClicked: root.toggledHeadingUp()
        Column {
            anchors.centerIn: parent; spacing: 2 * root.k
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "AUTO"; font.bold: true
                color: root.headingUp ? root.accent : "#ffffff"; font.pixelSize: 17 * root.k; font.family: root.fontFamily }
            Glyph { kind: "mapfold"; color: root.headingUp ? root.accent : "#ffffff"; width: 34 * root.k; height: width
                anchors.horizontalCenter: parent.horizontalCenter }
        }
    }
    MapButton {
        id: btnGear
        width: root.btn; height: root.btn; x: btn2d.x; y: root.height - height - root.edge
        Glyph { kind: "gear"; color: "#ffffff"; width: 40 * root.k; height: width; anchors.centerIn: parent }
    }
    MapButton {
        width: root.btn; height: root.btn; x: btn2d.x; y: btnGear.y - root.btn - 14 * root.k
        Glyph { kind: "traffic"; color: "#ffffff"; width: 40 * root.k; height: width; anchors.centerIn: parent }
    }

    // ================= floating card, lower left =================
    Rectangle {
        id: card
        x: root.edge; y: root.height - height - root.edge
        width: Math.round(Math.max(360, Math.min(580, root.width * 0.34)))
        height: infoRow.height + searchRow.height + iconRow.height + 2
        radius: 20 * root.k; color: "#f2313236"; clip: true

        Item {   // route info: next turn + remaining loop distance
            id: infoRow
            width: parent.width; height: 88 * root.k
            Glyph {
                id: turnGlyph
                kind: "turn"; angle: root.info && root.info.turn ? root.info.turn.angle : 0
                color: "#ffffff"; visible: root.info !== null && root.info.turn !== null
                width: 52 * root.k; height: width; x: 24 * root.k; anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                anchors { left: turnGlyph.right; leftMargin: 18 * root.k; verticalCenter: parent.verticalCenter }
                text: root.info && root.info.turn ? Geo.formatDistance(root.info.turn.distance) : "--"
                color: "#ffffff"; font.bold: true; font.pixelSize: 40 * root.k; font.family: root.fontFamily
            }
            Column {
                anchors { right: parent.right; rightMargin: 26 * root.k; verticalCenter: parent.verticalCenter }
                spacing: 0
                Text { anchors.right: parent.right; color: "#ffffff"; font.pixelSize: 32 * root.k; font.family: root.fontFamily
                    text: root.info ? Geo.formatDistance(root.info.remaining) : "--" }
                Text { anchors.right: parent.right; color: "#9c9da5"; font.pixelSize: 20 * root.k; font.family: root.fontFamily; text: "remaining" }
            }
        }
        Rectangle { y: infoRow.height; width: parent.width; height: 1; color: "#3f4045" }

        Item {
            id: searchRow
            y: infoRow.height + 1; width: parent.width; height: 70 * root.k
            Glyph { kind: "search"; color: "#c4c5cb"; width: 34 * root.k; height: width; x: 30 * root.k; anchors.verticalCenter: parent.verticalCenter }
            Text { x: 82 * root.k; anchors.verticalCenter: parent.verticalCenter; text: root.searchText
                color: "#c4c5cb"; font.pixelSize: 30 * root.k; font.family: root.fontFamily }
        }
        Rectangle { y: searchRow.y + searchRow.height; width: parent.width; height: 1; color: "#3f4045" }

        Row {
            id: iconRow
            y: searchRow.y + searchRow.height + 1; width: parent.width; height: 82 * root.k
            Repeater {
                model: [{ k: "home", c: "#ffffff" }, { k: "building", c: "#75767c" }, { k: "star", c: "#ffffff" },
                        { k: "fuel", c: "#ffffff" }, { k: "parking", c: "#ffffff" }]
                Item {
                    width: iconRow.width / 5; height: iconRow.height
                    Glyph { kind: modelData.k; color: modelData.c; width: 42 * root.k; height: width; anchors.centerIn: parent }
                }
            }
        }
    }

    // OSM attribution (required by the ODbL), quiet and out of the way
    Rectangle {
        visible: attribution.text !== ""
        anchors { right: btn2d.left; rightMargin: 14 * root.k; bottom: parent.bottom; bottomMargin: 8 * root.k }
        width: attribution.width + 20 * root.k; height: attribution.height + 8 * root.k; radius: 8 * root.k
        color: "#99111518"
        Text {
            id: attribution
            anchors.centerIn: parent
            width: Math.min(implicitWidth, root.width - card.width - root.btn - 5 * root.edge)
            elide: Text.ElideRight
            text: root.mapData && root.mapData.attribution ? root.mapData.attribution : ""
            color: "#8d909b"; opacity: 0.8; font.pixelSize: 24; font.family: root.fontFamily
        }
    }

    // ================= private components =================
    component MapButton: Rectangle {
        id: b
        signal clicked()
        radius: width * 0.2
        color: ma.pressed ? "#44454b" : "#f2313236"
        MouseArea { id: ma; anchors.fill: parent; onClicked: b.clicked() }
    }

    // Simple shape glyphs, drawn on a 100x100 box; repainted only when kind/colour/angle change.
    component Glyph: Canvas {
        id: g
        property string kind: ""
        property color color: "#ffffff"
        property real angle: 0                    // "turn" only: degrees, + = right
        onKindChanged: requestPaint()
        onColorChanged: requestPaint()
        onAngleChanged: requestPaint()

        function rounded(ctx, x, y, w, h, r) {    // clockwise rounded rectangle
            ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r)
            ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
            ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r)
            ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r); ctx.closePath()
        }
        function hole(ctx, x, y, w, h) {          // counter-clockwise rectangle: a hole under non-zero fill
            ctx.moveTo(x, y); ctx.lineTo(x, y + h); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w, y); ctx.closePath()
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.scale(width / 100, height / 100)
            ctx.fillStyle = color; ctx.strokeStyle = color
            ctx.lineCap = "round"; ctx.lineJoin = "round"
            var i
            ctx.beginPath()
            switch (kind) {
            case "navarrow":
                ctx.moveTo(50, 4); ctx.lineTo(90, 92); ctx.lineTo(50, 72); ctx.lineTo(10, 92); ctx.closePath(); ctx.fill()
                break
            case "home":
                ctx.moveTo(50, 8); ctx.lineTo(96, 50); ctx.lineTo(84, 50); ctx.lineTo(84, 92); ctx.lineTo(61, 92)
                ctx.lineTo(61, 62); ctx.lineTo(39, 62); ctx.lineTo(39, 92); ctx.lineTo(16, 92); ctx.lineTo(16, 50)
                ctx.lineTo(4, 50); ctx.closePath(); ctx.fill()
                break
            case "building":
                rounded(ctx, 18, 6, 64, 88, 4)
                for (i = 0; i < 3; i++) { hole(ctx, 30, 18 + i * 22, 14, 14); hole(ctx, 56, 18 + i * 22, 14, 14) }
                hole(ctx, 43, 78, 14, 16)
                ctx.fill()
                break
            case "star":
                for (i = 0; i < 10; i++) {
                    var a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 === 0 ? 48 : 21
                    if (i === 0) ctx.moveTo(50 + rr * Math.cos(a), 52 + rr * Math.sin(a))
                    else ctx.lineTo(50 + rr * Math.cos(a), 52 + rr * Math.sin(a))
                }
                ctx.closePath(); ctx.fill()
                break
            case "fuel":
                rounded(ctx, 12, 6, 52, 88, 7); hole(ctx, 22, 17, 32, 24)
                ctx.fill()
                ctx.beginPath()
                ctx.lineWidth = 8
                ctx.moveTo(64, 30); ctx.lineTo(78, 40); ctx.lineTo(78, 74); ctx.arc(87, 74, 9, Math.PI, 0, true); ctx.lineTo(96, 36); ctx.lineTo(84, 22)
                ctx.stroke()
                break
            case "parking":
                ctx.lineWidth = 17; ctx.lineJoin = "miter"; ctx.lineCap = "butt"
                ctx.moveTo(32, 94); ctx.lineTo(32, 10); ctx.lineTo(54, 10); ctx.arc(54, 34, 24, -Math.PI / 2, Math.PI / 2)
                ctx.lineTo(32, 58)
                ctx.stroke()
                break
            case "search":
                ctx.lineWidth = 8; ctx.arc(42, 42, 28, 0, Math.PI * 2); ctx.stroke()
                ctx.beginPath(); ctx.lineWidth = 9; ctx.moveTo(64, 64); ctx.lineTo(92, 92); ctx.stroke()
                break
            case "gear":
                for (i = 0; i < 8; i++) {
                    var t = i * Math.PI / 4, d = Math.PI / 180, pts = [[-9, 48], [9, 48], [15, 36], [30, 36]]
                    for (var j = 0; j < 4; j++) {
                        var an = t + pts[j][0] * d, r2 = pts[j][1]
                        if (i === 0 && j === 0) ctx.moveTo(50 + r2 * Math.cos(an), 50 + r2 * Math.sin(an))
                        else ctx.lineTo(50 + r2 * Math.cos(an), 50 + r2 * Math.sin(an))
                    }
                }
                ctx.closePath()
                ctx.moveTo(67, 50); ctx.arc(50, 50, 17, 0, Math.PI * 2, true)
                ctx.fill()
                break
            case "traffic":
                rounded(ctx, 24, 4, 52, 92, 12)
                for (i = 0; i < 3; i++) { ctx.moveTo(50 + 10, 24 + i * 26); ctx.arc(50, 24 + i * 26, 10, 0, Math.PI * 2, true) }
                ctx.fill()
                break
            case "mapfold":
                ctx.lineWidth = 7
                ctx.moveTo(8, 22); ctx.lineTo(36, 12); ctx.lineTo(64, 24); ctx.lineTo(92, 14); ctx.lineTo(92, 78)
                ctx.lineTo(64, 88); ctx.lineTo(36, 76); ctx.lineTo(8, 86); ctx.closePath()
                ctx.moveTo(36, 12); ctx.lineTo(36, 76); ctx.moveTo(64, 24); ctx.lineTo(64, 88)
                ctx.stroke()
                break
            case "turn":
                // stem going up, then a branch bent by `angle`, with an arrow head
                var rad = Math.max(-170, Math.min(170, angle)) * Math.PI / 180
                var ex = 50 + 42 * Math.sin(rad), ey = 44 - 42 * Math.cos(rad)
                ctx.lineWidth = 12
                ctx.moveTo(50, 96); ctx.lineTo(50, 44); ctx.lineTo(50 + 30 * Math.sin(rad), 44 - 30 * Math.cos(rad))
                ctx.stroke()
                ctx.beginPath()
                var ux = Math.sin(rad), uy = -Math.cos(rad)         // branch direction on screen
                ctx.moveTo(ex + ux * 14, ey + uy * 14)
                ctx.lineTo(ex - uy * 20 - ux * 6, ey + ux * 20 - uy * 6)
                ctx.lineTo(ex + uy * 20 - ux * 6, ey - ux * 20 - uy * 6)
                ctx.closePath(); ctx.fill()
                break
            }
        }
    }
}
