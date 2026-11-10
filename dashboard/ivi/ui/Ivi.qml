import QtQuick 2.15

// Mock IVI screen. Roads/route © OpenStreetMap contributors (ODbL), drawn locally (no tile server).
// This file ships inside each IVI release; the cockpit display loads whichever slot is current.
Item {
    id: root
    width: 1920; height: 600
    property string runDir: "file:///run/capstone-ivi/"
    property var vstatus: ({})
    property var nav: ({ x: 0, y: 0, heading: 0, speed: 0 })
    property var mapData: null
    property var classes: ({})
    readonly property bool mapActive: vstatus.map_active === true
    readonly property bool mapEnabled: vstatus.map_enabled === true
    readonly property string version: vstatus.version ? vstatus.version : "…"
    readonly property color accent: version === "V3" ? "#ff9f43" : "#27d3a2"
    readonly property color accentB: version === "V3" ? "#9b59ff" : "#2b59c3"

    function readJson(url, done) {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState === 4) { try { done(JSON.parse(x.responseText)) } catch (e) { } }
        }
        x.open("GET", url); x.send()
    }
    function prepareMap(d) {
        var groups = {}
        for (var i = 0; i < d.roads.length; i++) {
            var r = d.roads[i], p = r.p, minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9
            for (var k = 0; k < p.length; k += 2) {
                if (p[k] < minx) minx = p[k]; if (p[k] > maxx) maxx = p[k]
                if (p[k + 1] < miny) miny = p[k + 1]; if (p[k + 1] > maxy) maxy = p[k + 1]
            }
            r.b = [minx, miny, maxx, maxy]
            if (!groups[r.c]) groups[r.c] = []
            groups[r.c].push(r)
        }
        classes = groups; mapData = d
    }
    Component.onCompleted: readJson(Qt.resolvedUrl("../map.json"), prepareMap)
    Timer { interval: 200; running: true; repeat: true
        onTriggered: readJson(root.runDir + "nav.json", function (d) { root.nav = d; if (root.mapActive) canvas.requestPaint() }) }
    Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: readJson(root.runDir + "status.json", function (d) { root.vstatus = d }) }
    Timer { id: clock; interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: clockText.text = Qt.formatTime(new Date(), "hh:mm") }

    Rectangle { anchors.fill: parent; color: "#0b0f17" }
    Rectangle { z: 5; anchors { left: parent.left; right: parent.right; top: parent.top } height: 6; color: root.accent }

    // ---- left rail ----
    Rectangle {
        id: rail; width: 96; height: parent.height; color: "#10161f"
        Column {
            anchors.horizontalCenter: parent.horizontalCenter; y: 18; spacing: 14
            Repeater {
                model: [{ n: "Home", a: !root.mapActive }, { n: "Map", a: root.mapActive, off: !root.mapEnabled }, { n: "Music", a: false },
                        { n: "Phone", a: false }, { n: "Setup", a: false }]
                Rectangle {
                    width: 72; height: 72; radius: 18; opacity: modelData.off ? 0.35 : 1.0; color: modelData.a ? Qt.rgba(0.15, 0.83, 0.64, 0.18) : "transparent"
                    border.color: modelData.a ? root.accent : "#232b38"; border.width: modelData.a ? 2 : 1
                    Rectangle { width: 22; height: 22; radius: modelData.n === "Map" ? 2 : 11; anchors.horizontalCenter: parent.horizontalCenter
                        y: 14; color: "transparent"; border.width: 2; border.color: modelData.a ? root.accent : "#6c7a8f"
                        rotation: modelData.n === "Map" ? 45 : 0 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; y: 46; text: modelData.n; font.pixelSize: 13
                        color: modelData.a ? "#e8f1ff" : "#7d8aa0" }
                }
            }
        }
    }

    // ---- top bar ----
    Rectangle {
        id: topbar; anchors { left: rail.right; right: parent.right; top: parent.top } height: 64; color: "#10161f"
        Text { id: clockText; anchors { left: parent.left; leftMargin: 28; verticalCenter: parent.verticalCenter }
            color: "#e8f1ff"; font.pixelSize: 26; font.bold: true }
        Text { anchors.centerIn: parent; text: root.mapActive ? "Navigation" : "Home"; color: "#9fb0c8"; font.pixelSize: 22 }
        Row {
            anchors { right: parent.right; rightMargin: 24; verticalCenter: parent.verticalCenter } spacing: 12
            Rectangle {   // transparent about the studied load: shows the prefetch workers that cause interference
                visible: root.vstatus.workers > 0; height: 36; width: prefetchText.width + 28; radius: 18
                color: "#2a1f12"; border.color: "#ff9f43"
                Text { id: prefetchText; anchors.centerIn: parent; color: "#ffb86b"; font.pixelSize: 15
                    text: "Tile prefetch ×" + root.vstatus.workers + (root.vstatus.priority === "rr10" ? "  ·  realtime" : "") }
            }
            Rectangle { height: 44; width: 190; radius: 22; color: root.accent
                Text { anchors.centerIn: parent; color: "#0b0f17"; font.pixelSize: 24; font.bold: true; text: "IVI " + root.version } }
        }
    }

    // ---- home screen ----
    Item {
        visible: !root.mapActive
        anchors { left: rail.right; right: parent.right; top: topbar.bottom; bottom: parent.bottom; margins: 28 }
        Row {
            anchors.fill: parent; spacing: 24
            Rectangle { width: parent.width * 0.42; height: parent.height; radius: 26; color: "#141c28"; border.color: "#232b38"
                Text { x: 32; y: 28; text: "NOW PLAYING"; color: "#7d8aa0"; font.pixelSize: 15; font.letterSpacing: 2 }
                Rectangle { x: 32; y: 70; width: 190; height: 190; radius: 22
                    gradient: Gradient { GradientStop { position: 0; color: root.accent } GradientStop { position: 1; color: root.accentB } } }
                Text { x: 250; y: 96; text: "Night Drive"; color: "#e8f1ff"; font.pixelSize: 36; font.bold: true }
                Text { x: 250; y: 150; text: "Capstone Radio"; color: "#9fb0c8"; font.pixelSize: 22 }
                Rectangle { x: 32; y: 300; width: parent.width - 64; height: 6; radius: 3; color: "#232b38"
                    Rectangle { id: progress; height: parent.height; radius: 3; color: root.accent; width: 0
                        NumberAnimation on width { from: 0; to: progress.parent.width; duration: 180000; loops: Animation.Infinite } } }
            }
            Column { width: parent.width * 0.55; height: parent.height; spacing: 24
                Rectangle { width: parent.width; height: (parent.height - 24) * 0.55; radius: 26; color: "#141c28"; border.color: "#232b38"
                    Text { x: 32; y: 28; text: "NAVIGATION"; color: "#7d8aa0"; font.pixelSize: 15; font.letterSpacing: 2 }
                    Text { x: 32; y: 70; text: root.mapEnabled ? "Seoul City Hall loop" : "No navigation map"; color: root.mapEnabled ? "#e8f1ff" : "#7d8aa0"; font.pixelSize: 34; font.bold: true }
                    Text { x: 32; y: 124; text: root.mapEnabled ? "5.6 km  ·  open Map to start guidance" : "This version of the head unit has no map"; color: "#9fb0c8"; font.pixelSize: 20 }
                    Text { x: 32; y: 170; font.pixelSize: 20; font.bold: true; color: root.accent
                        text: root.mapEnabled ? "New in V3  ·  navigation map with background tile prefetch" : "Standard head unit" }
                }
                Row { width: parent.width; height: (parent.height - 24) * 0.45; spacing: 24
                    Rectangle { width: (parent.width - 24) / 2; height: parent.height; radius: 26; color: "#141c28"; border.color: "#232b38"
                        Text { x: 28; y: 24; text: "CLIMATE"; color: "#7d8aa0"; font.pixelSize: 15; font.letterSpacing: 2 }
                        Text { x: 28; y: 60; text: "21.5°"; color: "#e8f1ff"; font.pixelSize: 44; font.bold: true } }
                    Rectangle { width: (parent.width - 24) / 2; height: parent.height; radius: 26; color: "#141c28"; border.color: "#232b38"
                        Text { x: 28; y: 24; text: "PHONE"; color: "#7d8aa0"; font.pixelSize: 15; font.letterSpacing: 2 }
                        Text { x: 28; y: 60; text: "No device"; color: "#9fb0c8"; font.pixelSize: 30 } }
                }
            }
        }
    }

    // ---- map screen ----
    Item {
        visible: root.mapActive
        anchors { left: rail.right; right: parent.right; top: topbar.bottom; bottom: parent.bottom }
        Canvas {
            id: canvas; anchors.fill: parent; renderTarget: Canvas.Image
            readonly property var styles: ({
                motorway: ["#7b88ad", 7], trunk: ["#7b88ad", 7], primary: ["#5d6c91", 6], primary_link: ["#5d6c91", 4],
                secondary: ["#47547a", 5], secondary_link: ["#47547a", 3], tertiary: ["#38445f", 4], tertiary_link: ["#38445f", 3],
                residential: ["#2a3447", 3], unclassified: ["#2a3447", 3] })
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                ctx.fillStyle = "#0d121b"; ctx.fillRect(0, 0, width, height)
                if (!root.mapData) return
                var s = 0.9, cx = width / 2, cy = height * 0.74, R = Math.hypot(width, height) / s
                var px = root.nav.x, py = root.nav.y
                ctx.save()
                ctx.translate(cx, cy); ctx.rotate(-root.nav.heading * Math.PI / 180); ctx.scale(s, -s); ctx.translate(-px, -py)
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                var order = ["residential", "unclassified", "tertiary_link", "tertiary", "secondary_link", "secondary",
                             "primary_link", "primary", "trunk", "motorway"]
                for (var o = 0; o < order.length; o++) {
                    var list = root.classes[order[o]]
                    if (!list) continue
                    var st = styles[order[o]]
                    ctx.beginPath()
                    for (var i = 0; i < list.length; i++) {
                        var b = list[i].b
                        if (b[2] < px - R || b[0] > px + R || b[3] < py - R || b[1] > py + R) continue
                        var p = list[i].p
                        ctx.moveTo(p[0], p[1])
                        for (var k = 2; k < p.length; k += 2) ctx.lineTo(p[k], p[k + 1])
                    }
                    ctx.strokeStyle = st[0]; ctx.lineWidth = st[1] / s; ctx.stroke()
                }
                var rp = root.mapData.route.p
                ctx.beginPath(); ctx.moveTo(rp[0], rp[1])
                for (var j = 2; j < rp.length; j += 2) ctx.lineTo(rp[j], rp[j + 1])
                ctx.strokeStyle = "rgba(39,211,162,0.28)"; ctx.lineWidth = 16 / s; ctx.stroke()
                ctx.strokeStyle = "#27d3a2"; ctx.lineWidth = 6 / s; ctx.stroke()
                ctx.restore()
                // vehicle marker (fixed on screen, map rotates under it)
                ctx.fillStyle = "rgba(255,255,255,0.15)"; ctx.beginPath(); ctx.arc(cx, cy, 26, 0, Math.PI * 2); ctx.fill()
                ctx.fillStyle = "#ffffff"; ctx.beginPath()
                ctx.moveTo(cx, cy - 20); ctx.lineTo(cx + 14, cy + 14); ctx.lineTo(cx, cy + 7); ctx.lineTo(cx - 14, cy + 14); ctx.closePath(); ctx.fill()
            }
        }
        Rectangle { x: 28; y: 24; width: 330; height: 118; radius: 22; color: "#cc10161f"; border.color: "#232b38"
            Text { x: 24; y: 18; text: "Seoul City Hall loop"; color: "#e8f1ff"; font.pixelSize: 24; font.bold: true }
            Text { x: 24; y: 58; text: "Follow the highlighted route"; color: "#9fb0c8"; font.pixelSize: 18 }
            Text { x: 24; y: 84; text: "5.6 km"; color: "#27d3a2"; font.pixelSize: 18; font.bold: true } }
        Rectangle { anchors { right: parent.right; rightMargin: 28; bottom: parent.bottom; bottomMargin: 28 }
            width: 190; height: 110; radius: 22; color: "#cc10161f"; border.color: "#232b38"
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 12; color: "#e8f1ff"; font.pixelSize: 56; font.bold: true
                text: Math.round(root.nav.speed) }
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 78; text: "km/h"; color: "#7d8aa0"; font.pixelSize: 18 } }
        Text { anchors { left: parent.left; leftMargin: 28; bottom: parent.bottom; bottomMargin: 10 }
            text: root.mapData ? root.mapData.attribution : ""; color: "#4f5b70"; font.pixelSize: 12 }
    }
}
