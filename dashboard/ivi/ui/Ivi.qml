import QtQuick 2.15

// Real IVI head unit. Roads/route © OpenStreetMap contributors (ODbL), drawn locally (no tile server).
// This file ships inside each IVI release (identical for V2/V3); the cockpit display loads whichever
// slot is current. Visual language shared with Cockpit.qml: one signature accent (the same orange
// pulled from the real cluster gauge), flat panels with hairline dividers rather than SaaS cards,
// Space Grotesk for headlines/numerals, DM Sans for labels. The version badge in the topbar is the
// one dynamic indicator here -- it rolls like an odometer digit when the running version changes,
// the way a real trip computer flips over, rather than a full-screen takeover.
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
    readonly property color accent: "#ff7a1a"        // single signature hue, shared with Cockpit.qml
    readonly property color warn: "#ffcc33"
    readonly property color ink: "#eef1f6"
    readonly property color inkMuted: "#7c8696"
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.09)

    FontLoader { id: fGroteskBold; source: "fonts/SpaceGrotesk-Bold.ttf" }
    FontLoader { id: fGroteskMed; source: "fonts/SpaceGrotesk-Medium.ttf" }
    FontLoader { id: fSans; source: "fonts/DMSans-Regular.ttf" }
    FontLoader { id: fSansMed; source: "fonts/DMSans-Medium.ttf" }
    FontLoader { id: fMono; source: "fonts/DMMono-Medium.ttf" }

    property string _lastVersion: ""
    onVersionChanged: {
        if (_lastVersion !== "" && _lastVersion !== version) verBadge.rollTo(version)
        _lastVersion = version
    }

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
    Rectangle { z: 5; anchors { left: parent.left; right: parent.right; top: parent.top } height: 4; color: root.accent }

    // ---- left rail ----
    Rectangle {
        id: rail; width: 96; height: parent.height; color: "#0e131a"
        Rectangle { anchors { right: parent.right; top: parent.top; bottom: parent.bottom } width: 1; color: root.hairline }
        Column {
            anchors.horizontalCenter: parent.horizontalCenter; y: 18; spacing: 14
            Repeater {
                model: [{ n: "Home", a: !root.mapActive }, { n: "Map", a: root.mapActive, off: !root.mapEnabled }, { n: "Music", a: false },
                        { n: "Phone", a: false }, { n: "Setup", a: false }]
                Rectangle {
                    width: 72; height: 72; radius: 4; opacity: modelData.off ? 0.35 : 1.0; color: "transparent"
                    Rectangle { visible: modelData.a; anchors { left: parent.left; top: parent.top; bottom: parent.bottom } width: 3; color: root.accent }
                    Rectangle { width: 22; height: 22; radius: modelData.n === "Map" ? 2 : 11; anchors.horizontalCenter: parent.horizontalCenter
                        y: 14; color: "transparent"; border.width: 2; border.color: modelData.a ? root.accent : "#555f6e"
                        rotation: modelData.n === "Map" ? 45 : 0 }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; y: 46; text: modelData.n; font.pixelSize: 13; font.family: fSansMed.name
                        color: modelData.a ? root.ink : root.inkMuted }
                }
            }
        }
    }

    // ---- top bar ----
    Rectangle {
        id: topbar; anchors { left: rail.right; right: parent.right; top: parent.top } height: 64; color: "#0e131a"
        Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: 1; color: root.hairline }
        Text { id: clockText; anchors { left: parent.left; leftMargin: 28; verticalCenter: parent.verticalCenter }
            color: root.ink; font.pixelSize: 24; font.family: fMono.name }
        Text { anchors.centerIn: parent; text: root.mapActive ? "NAVIGATION" : "HOME"; color: root.inkMuted; font.pixelSize: 15; font.letterSpacing: 2; font.family: fSansMed.name }
        Row {
            anchors { right: parent.right; rightMargin: 24; verticalCenter: parent.verticalCenter } spacing: 12
            Row {   // flat technical tag, not a filled rounded pill -- shows the prefetch workers that cause interference
                visible: root.vstatus.workers > 0; spacing: 8; anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 3; height: 20; color: root.warn; anchors.verticalCenter: parent.verticalCenter }
                Text { anchors.verticalCenter: parent.verticalCenter; color: root.warn; font.pixelSize: 14; font.family: fMono.name
                    text: "PREFETCH ×" + root.vstatus.workers + (root.vstatus.priority === "rr10" ? " · RT" : "") }
            }
            // ---- version badge: rolls like an odometer digit when the running version changes ----
            Rectangle {
                id: verBadge
                height: 40; width: 112; color: root.accent
                anchors.verticalCenter: parent.verticalCenter
                property string shown: root.version
                function rollTo(newVersion) {
                    newVerText.text = "IVI " + newVersion
                    newVerText.y = 28; oldVerText.y = 0
                    pulse.restart(); rollSeq.restart()
                }
                SequentialAnimation { id: pulse
                    NumberAnimation { target: verBadge; property: "scale"; to: 1.08; duration: 120; easing.type: Easing.OutQuad }
                    NumberAnimation { target: verBadge; property: "scale"; to: 1.0; duration: 220; easing.type: Easing.OutBack; easing.overshoot: 2.2 } }
                Item {
                    anchors.centerIn: parent; width: 96; height: 26; clip: true
                    Text { id: oldVerText; anchors.horizontalCenter: parent.horizontalCenter
                        text: "IVI " + verBadge.shown; color: "#05070a"; font.family: fGroteskBold.name; font.pixelSize: 20 }
                    Text { id: newVerText; anchors.horizontalCenter: parent.horizontalCenter; y: 28
                        text: ""; color: "#05070a"; font.family: fGroteskBold.name; font.pixelSize: 20 }
                    SequentialAnimation { id: rollSeq
                        ParallelAnimation {
                            NumberAnimation { target: oldVerText; property: "y"; to: -28; duration: 420; easing.type: Easing.InCubic }
                            NumberAnimation { target: newVerText; property: "y"; to: 0; duration: 460; easing.type: Easing.OutBack; easing.overshoot: 1.6 }
                        }
                        ScriptAction { script: { verBadge.shown = root.version; oldVerText.y = 0 } }
                    }
                }
            }
        }
    }

    // ---- home screen ----
    Item {
        visible: !root.mapActive
        anchors { left: rail.right; right: parent.right; top: topbar.bottom; bottom: parent.bottom; margins: 28 }
        Row {
            anchors.fill: parent; spacing: 24
            Rectangle { width: parent.width * 0.42; height: parent.height; color: "#10151c"
                Rectangle { width: 2; height: parent.height; color: root.accent }
                Text { x: 32; y: 28; text: "NOW PLAYING"; color: root.inkMuted; font.pixelSize: 15; font.letterSpacing: 2; font.family: fSansMed.name }
                Rectangle { x: 32; y: 70; width: 190; height: 190; color: "#1a212b"
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 3; color: root.accent } }
                Text { x: 250; y: 96; text: "Night Drive"; color: root.ink; font.pixelSize: 34; font.family: fGroteskBold.name }
                Text { x: 250; y: 150; text: "Capstone Radio"; color: root.inkMuted; font.pixelSize: 20; font.family: fSans.name }
                Rectangle { x: 32; y: 300; width: parent.width - 64; height: 2; color: "#232b38"
                    Rectangle { id: progress; height: parent.height; color: root.accent; width: 0
                        NumberAnimation on width { from: 0; to: progress.parent.width; duration: 180000; loops: Animation.Infinite } } }
            }
            Column { width: parent.width * 0.55; height: parent.height; spacing: 24
                Rectangle { width: parent.width; height: (parent.height - 24) * 0.55; color: "#10151c"
                    Rectangle { width: parent.width; height: 1; color: root.hairline; y: 0 }
                    Text { x: 32; y: 28; text: "NAVIGATION"; color: root.inkMuted; font.pixelSize: 15; font.letterSpacing: 2; font.family: fSansMed.name }
                    Text { x: 32; y: 70; text: root.mapEnabled ? "Seoul City Hall loop" : "No navigation map"; color: root.mapEnabled ? root.ink : root.inkMuted; font.pixelSize: 32; font.family: fGroteskBold.name }
                    Text { x: 32; y: 124; text: root.mapEnabled ? "5.6 km  ·  open Map to start guidance" : "This version of the head unit has no map"; color: root.inkMuted; font.pixelSize: 19; font.family: fSans.name }
                    Text { x: 32; y: 170; font.pixelSize: 18; font.family: fSansMed.name; color: root.accent
                        text: root.mapEnabled ? "New in V3  ·  navigation map with background tile prefetch" : "Standard head unit" }
                }
                Row { width: parent.width; height: (parent.height - 24) * 0.45; spacing: 24
                    Rectangle { width: (parent.width - 24) / 2; height: parent.height; color: "#10151c"
                        Text { x: 28; y: 24; text: "CLIMATE"; color: root.inkMuted; font.pixelSize: 15; font.letterSpacing: 2; font.family: fSansMed.name }
                        Text { x: 28; y: 60; text: "21.5°"; color: root.ink; font.pixelSize: 42; font.family: fGroteskBold.name } }
                    Rectangle { width: (parent.width - 24) / 2; height: parent.height; color: "#10151c"
                        Text { x: 28; y: 24; text: "PHONE"; color: root.inkMuted; font.pixelSize: 15; font.letterSpacing: 2; font.family: fSansMed.name }
                        Text { x: 28; y: 60; text: "No device"; color: root.inkMuted; font.pixelSize: 28; font.family: fSans.name } }
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
                ctx.strokeStyle = "rgba(255,122,26,0.28)"; ctx.lineWidth = 16 / s; ctx.stroke()
                ctx.strokeStyle = "#ff7a1a"; ctx.lineWidth = 6 / s; ctx.stroke()
                ctx.restore()
                // vehicle marker (fixed on screen, map rotates under it)
                ctx.fillStyle = "rgba(255,255,255,0.15)"; ctx.beginPath(); ctx.arc(cx, cy, 26, 0, Math.PI * 2); ctx.fill()
                ctx.fillStyle = "#ffffff"; ctx.beginPath()
                ctx.moveTo(cx, cy - 20); ctx.lineTo(cx + 14, cy + 14); ctx.lineTo(cx, cy + 7); ctx.lineTo(cx - 14, cy + 14); ctx.closePath(); ctx.fill()
            }
        }
        Rectangle { x: 28; y: 24; width: 330; height: 118; color: "#cc10161f"
            Rectangle { width: 2; height: parent.height; color: root.accent }
            Text { x: 24; y: 18; text: "Seoul City Hall loop"; color: root.ink; font.pixelSize: 22; font.family: fGroteskBold.name }
            Text { x: 24; y: 58; text: "Follow the highlighted route"; color: root.inkMuted; font.pixelSize: 17; font.family: fSans.name }
            Text { x: 24; y: 84; text: "5.6 km"; color: root.accent; font.pixelSize: 17; font.family: fSansMed.name } }
        Rectangle { anchors { right: parent.right; rightMargin: 28; bottom: parent.bottom; bottomMargin: 28 }
            width: 190; height: 110; color: "#cc10161f"
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 10; color: root.ink; font.pixelSize: 52; font.family: fGroteskBold.name
                text: Math.round(root.nav.speed) }
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 78; text: "km/h"; color: root.inkMuted; font.pixelSize: 17; font.family: fSans.name } }
        Text { anchors { left: parent.left; leftMargin: 28; bottom: parent.bottom; bottomMargin: 10 }
            text: root.mapData ? root.mapData.attribution : ""; color: "#4f5b70"; font.pixelSize: 12; font.family: fSans.name }
    }
}
