import QtQuick 2.15
import "."
import "ivi.js" as H

// IVI head unit, 1920x600 design size. Roads/route (c) OpenStreetMap contributors (ODbL), drawn locally.
// Frame (top bar, speed column, dock) follows the reference head unit; the centre stage is the map (builds with
// a map, with the route, position marker and turn card computed from map.json + nav.json) or a media player
// panel (builds without a map). Live data: speed (nav.json, CAN 0x200), clock, status.json (version, CAN link,
// map feature, map workers). Everything else in the frame is visual. All sizes and colours come from Theme.qml.
Item {
    id: root
    width: 1920; height: 600

    // ---- interface used by the cockpit ----
    property string runDir: "file:///run/capstone-ivi/"
    property var vstatus: ({})
    property var nav: ({})
    readonly property bool mapActive: vstatus.map_active === true

    // ---- state ----
    property var mapData: null
    property var classes: ({})
    property var routeCum: []
    property string page: "home"          // "home" (media player) | "map"
    property bool headingUp: true         // AUTO button: heading-up vs north-up map
    property bool playing: true
    property int track: 0
    property real elapsed: 0
    property real volume: 0.6
    readonly property var tracks: [
        { title: "새벽 드라이브", artist: "밤하늘 합주단", length: 214, c1: "#9a6bff", c2: "#4a28b8" },
        { title: "푸른 골목", artist: "소리숲", length: 187, c1: "#4aa8ff", c2: "#2a3fb0" },
        { title: "느린 파도", artist: "하루와 달", length: 241, c1: "#c26bff", c2: "#5b2aa8" },
        { title: "도시의 끝에서", artist: "낮은 구름", length: 198, c1: "#7a7bff", c2: "#2f2a9a" },
        { title: "기차는 여섯 시에", artist: "작은 정거장", length: 226, c1: "#b07cff", c2: "#3d2a9f" } ]

    readonly property bool mapEnabled: vstatus.map_enabled === true
    readonly property bool canLive: vstatus.can === true
    readonly property int workers: vstatus.workers > 0 ? vstatus.workers : 0
    readonly property string version: vstatus.version ? vstatus.version : "…"
    readonly property var routeFlat: mapData && mapData.route ? mapData.route.p : null
    readonly property real routeLen: routeFlat ? H.routeLength(routeFlat) : 0
    readonly property var lap: H.lapProgress(nav.distance, routeLen)
    readonly property var turn: routeLen > 0 ? H.nextTurn(routeFlat, lap.into) : null
    readonly property var current: tracks[track]

    onMapEnabledChanged: page = mapEnabled ? "map" : "home"
    onMapActiveChanged: if (mapEnabled) page = mapActive ? "map" : page
    onPageChanged: if (page === "map") canvas.requestPaint()
    onHeadingUpChanged: canvas.requestPaint()

    // ---- data plumbing ----
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
        classes = groups
        routeCum = d.route && d.route.p ? H.cumulative(d.route.p) : []
        mapData = d
    }
    function stepTrack(dir) { track = H.stepTrack(track, tracks.length, dir); elapsed = 0 }
    Component.onCompleted: readJson(Qt.resolvedUrl("../map.json"), prepareMap)
    Timer { interval: 200; running: true; repeat: true
        onTriggered: readJson(root.runDir + "nav.json", function (d) { root.nav = d; if (root.page === "map") canvas.requestPaint() }) }
    Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: { readJson(root.runDir + "status.json", function (d) { root.vstatus = d }); clockLabel.text = H.clockText(new Date()) } }
    Timer { interval: 1000; running: root.playing; repeat: true
        onTriggered: {
            var t = H.playerTick(root.elapsed, root.current.length, true)
            if (t.ended) root.stepTrack(1); else root.elapsed = t.elapsed
        } }

    FontLoader { source: "fonts/SpaceGrotesk-Bold.ttf" }
    FontLoader { source: "fonts/DMSans-Regular.ttf" }
    FontLoader { source: "fonts/DMSans-Medium.ttf" }

    Rectangle { anchors.fill: parent; color: Theme.bg }

    // =============== top bar ===============
    Item {
        id: topBar; width: parent.width; height: Theme.topH
        Row {   // segmented strip along the top of the left column
            spacing: 6; y: 0
            Repeater { model: 7; Rectangle { width: (Theme.leftW - 6 * 6) / 7; height: 4; color: Theme.segment } }
        }
        Text { id: rpmNum; x: 16; y: 8; text: "0"; color: Theme.ink; font.pixelSize: Theme.tMd; font.family: Theme.fHead }
        Text { x: rpmNum.x + rpmNum.width + 2; anchors.baseline: rpmNum.baseline; text: "rpm"; color: Theme.ink
            font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
        Text { x: 316 - width / 2; y: 4; text: "P"; color: Theme.ink; font.pixelSize: Theme.tXl; font.family: Theme.fHead }
        Text { id: rangeText; x: 437; y: 14; text: "514km"; color: Theme.ink; font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
        Icon { x: 534; y: 14; kind: "fuel"; size: Theme.iconSm }
        Rectangle { x: 578; y: 18; width: 52; height: 28; radius: 6; color: Theme.segment
            Rectangle { width: 40; height: parent.height; radius: 6; color: Theme.ink } }

        Icon { x: 668 - width / 2; y: 14; kind: "search"; size: Theme.iconLg }
        Icon { x: 742 - width / 2; y: 14; kind: "bell"; size: Theme.iconLg }

        Rectangle {   // installed version (real, status.json)
            id: badge; x: 800; y: 10; height: 44; width: badgeText.width + 40; radius: 22; color: Theme.accent
            Text { id: badgeText; anchors.centerIn: parent; text: "버전 " + root.version; color: Theme.inkOnAccent
                font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
        }
        Row {   // CAN link (real, status.json): a build without a bus is neutral, a lost bus is an alarm
            x: badge.x + badge.width + Theme.s3; y: 14; spacing: 12
            Rectangle { width: 16; height: 16; radius: 8; anchors.verticalCenter: parent.verticalCenter
                color: root.canLive ? Theme.accent : (root.mapEnabled ? Theme.danger : Theme.inkMuted) }
            Text { font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed
                color: root.canLive ? Theme.ink : (root.mapEnabled ? Theme.danger : Theme.inkMuted)
                text: root.canLive ? "CAN 연결됨" : (root.mapEnabled ? "CAN 신호 끊김" : "CAN 신호 없음") }
        }

        Icon { x: 1392 - width / 2; y: 14; kind: "lte"; size: Theme.iconLg }
        Icon { x: 1462 - width / 2; y: 14; kind: "volume"; size: Theme.iconLg }
        Icon { x: 1526 - width / 2; y: 14; kind: "bluetooth"; size: Theme.iconLg }
        Icon { x: 1596 - width / 2; y: 8; kind: "person"; size: 48 }
        Text { id: clockLabel; anchors { right: parent.right; rightMargin: Theme.s2 } y: 10
            color: Theme.ink; font.pixelSize: Theme.tMd; font.family: Theme.fHead }
    }

    // =============== left column ===============
    Item {
        id: leftCol; y: Theme.topH; width: Theme.leftW; height: root.height - Theme.topH - Theme.dockH
        Text { id: speedNum; x: 14; y: -34; text: H.speedText(root.nav, root.canLive); color: root.canLive ? Theme.ink : Theme.overlayLine
            font.pixelSize: Theme.tNum; font.family: Theme.fHead }
        Text { x: 22; y: 228; text: "km/h"; color: Theme.inkMuted; font.pixelSize: Theme.tMd; font.family: Theme.fBody }

        Rectangle { x: 232; y: 238; width: 56; height: 56; radius: 28; color: Theme.accent
            Icon { anchors.centerIn: parent; kind: "lane"; size: Theme.iconSm; color: Theme.inkOnAccent } }
        Rectangle { x: 312; y: 238; width: 56; height: 56; radius: 28; color: Theme.overlayLine
            Icon { anchors.centerIn: parent; kind: "lock"; size: Theme.iconSm; knock: Theme.overlayLine } }
        Image { x: 150; y: 288; width: 330; height: Math.round(330 * 219 / 480); source: "car.png"; smooth: true }

        Text { x: 16; y: 396; text: "1576km"; color: Theme.ink; font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
        Icon { x: 312 - width / 2; y: 396; kind: "chevUp"; size: Theme.iconSm; color: Theme.inkMuted }
        Text { anchors.right: parent.right; anchors.rightMargin: Theme.s2; y: 396; text: "25°C"; color: Theme.ink
            font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
    }

    // =============== centre stage ===============
    Item {
        id: stage; x: 648; y: Theme.topH; width: root.width - 648 - 8; height: root.height - Theme.topH - Theme.dockH

        // ---- media player (builds without a map; visual only, no audio backend) ----
        Rectangle {
            anchors.fill: parent; radius: Theme.rPanel; color: Theme.surface; visible: root.page === "home"
            Rectangle {   // generated cover art
                id: cover; x: Theme.s4; y: 40; width: 360; height: 360; radius: Theme.rPanel; clip: true
                gradient: Gradient {
                    GradientStop { position: 0.0; color: root.current.c1 }
                    GradientStop { position: 1.0; color: root.current.c2 } }
                Rectangle { width: 300; height: 300; radius: 150; x: 90; y: 100; color: root.current.c2; opacity: 0.55 }
                Rectangle { width: 190; height: 190; radius: 95; x: 40; y: 40; color: "transparent"; border.width: 4; border.color: Theme.ink; opacity: 0.85 }
                Rectangle { width: 64; height: 64; radius: 32; x: 103; y: 103; color: Theme.ink; opacity: 0.9 }
            }
            Item {
                x: 428; y: 40; width: 460; height: 360
                Text { width: parent.width; elide: Text.ElideRight; text: root.current.title; color: Theme.ink
                    font.pixelSize: Theme.tXl; font.family: Theme.fHead }
                Text { y: 72; width: parent.width; elide: Text.ElideRight; text: root.current.artist; color: Theme.inkMuted
                    font.pixelSize: Theme.tSm; font.family: Theme.fBody }
                Rectangle {   // progress
                    id: bar; y: 168; width: parent.width; height: 8; radius: 4; color: Theme.overlayLine
                    Rectangle { width: parent.width * H.progressFraction(root.elapsed, root.current.length); height: parent.height; radius: 4; color: Theme.media }
                    MouseArea { anchors { fill: parent; topMargin: -16; bottomMargin: -16 }
                        onClicked: root.elapsed = Math.round(mouse.x / bar.width * root.current.length) }
                }
                Text { y: 188; text: H.formatTrackTime(root.elapsed); color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                Text { y: 188; anchors.right: parent.right; text: H.formatTrackTime(root.current.length); color: Theme.inkMuted
                    font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                Item {   // transport
                    y: 232; width: parent.width; height: 96
                    Icon { x: 90 - width / 2; anchors.verticalCenter: parent.verticalCenter; kind: "prev"; size: Theme.iconLg
                        MouseArea { anchors { fill: parent; margins: -16 } onClicked: root.stepTrack(-1) } }
                    Rectangle { x: 230 - 48; width: 96; height: 96; radius: 48
                        gradient: Gradient { GradientStop { position: 0.0; color: "#9a6bff" } GradientStop { position: 1.0; color: "#5a2fd8" } }
                        Icon { anchors.centerIn: parent; kind: root.playing ? "pause" : "play"; size: Theme.iconLg; knock: "#5a2fd8" }
                        MouseArea { anchors.fill: parent; onClicked: root.playing = !root.playing } }
                    Icon { x: 370 - width / 2; anchors.verticalCenter: parent.verticalCenter; kind: "next"; size: Theme.iconLg
                        MouseArea { anchors { fill: parent; margins: -16 } onClicked: root.stepTrack(1) } }
                }
                Row {   // volume
                    y: 330; spacing: Theme.s2; height: 36
                    Icon { kind: "volume"; size: Theme.iconSm; color: Theme.inkMuted }
                    Rectangle { id: vol; y: 14; width: 400; height: 8; radius: 4; color: Theme.overlayLine
                        Rectangle { width: parent.width * root.volume; height: parent.height; radius: 4; color: Theme.inkMuted }
                        MouseArea { anchors { fill: parent; topMargin: -14; bottomMargin: -14 }
                            onPressed: root.volume = Math.max(0, Math.min(1, mouse.x / vol.width))
                            onPositionChanged: if (pressed) root.volume = Math.max(0, Math.min(1, mouse.x / vol.width)) } }
                }
            }
            Rectangle { x: 920; y: 40; width: 1; height: 360; color: Theme.overlayLine }
            Item {   // next up
                x: 952; y: 32; width: 280; height: 380
                Text { text: "다음 곡"; color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
                Repeater {
                    model: 4
                    Column { y: 48 + index * 84; width: parent.width
                        readonly property var t: root.tracks[(root.track + 1 + index) % root.tracks.length]
                        Text { width: parent.width; elide: Text.ElideRight; text: parent.t.title; color: Theme.ink
                            font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
                        Text { width: parent.width; elide: Text.ElideRight; text: parent.t.artist; color: Theme.inkMuted
                            font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                    }
                }
            }
        }

        // ---- map page, build without a map ----
        Rectangle {
            anchors.fill: parent; radius: Theme.rPanel; color: Theme.surface; visible: root.page === "map" && !root.mapEnabled
            Text { anchors.centerIn: parent; width: parent.width - 160; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: "이 버전에는 내비게이션 지도가 없습니다"; color: Theme.ink; font.pixelSize: Theme.tXl; font.family: Theme.fHead }
        }

        // ---- map page ----
        Item {
            anchors.fill: parent; visible: root.page === "map" && root.mapEnabled
            Canvas {
                id: canvas; anchors.fill: parent; renderTarget: Canvas.Image
                // grey ramp by road class: major roads lighter and wider
                readonly property var styles: ({
                    motorway: ["#7a8194", 9], trunk: ["#7a8194", 9], primary: ["#646b7e", 7], primary_link: ["#646b7e", 5],
                    secondary: ["#50566a", 6], secondary_link: ["#50566a", 4], tertiary: ["#3e4354", 4.5], tertiary_link: ["#3e4354", 3],
                    residential: ["#2c303c", 3.5], unclassified: ["#2c303c", 3.5], service: ["#23262f", 2.5] })
                readonly property var areaColors: ({ water: "#0d1a2b", park: "#12201a", building: "#1a1c22" })
                function corners(ctx) {   // round the four corners by painting the screen colour outside the arcs
                    var r = Theme.rPanel
                    ctx.fillStyle = Theme.bg.toString()
                    var c = [[0, 0, 1, 1], [width, 0, -1, 1], [0, height, 1, -1], [width, height, -1, -1]]
                    for (var i = 0; i < 4; i++) {
                        var x = c[i][0], y = c[i][1], sx = c[i][2], sy = c[i][3]
                        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + sx * r, y)
                        ctx.arc(x + sx * r, y + sy * r, r, sy > 0 ? -Math.PI / 2 : Math.PI / 2, sx > 0 ? Math.PI : 0, sx * sy > 0)
                        ctx.closePath(); ctx.fill()
                    }
                }
                function trace(ctx, p, from, to) {
                    ctx.moveTo(p[from], p[from + 1])
                    for (var k = from + 2; k < to; k += 2) ctx.lineTo(p[k], p[k + 1])
                }
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.fillStyle = Theme.surface.toString(); ctx.fillRect(0, 0, width, height)
                    if (!root.mapData || root.nav.x === undefined) { corners(ctx); return }
                    // zoom widens with real speed; heading-up (AUTO) rotates the map under a fixed marker
                    var s = H.mapScale(root.canLive ? root.nav.speed : 0), cx = width * 0.5, cy = height * 0.74
                    var R = Math.hypot(width, height) / s
                    var px = root.nav.x, py = root.nav.y
                    var hr = root.nav.heading * Math.PI / 180, rot = root.headingUp ? -hr : 0
                    ctx.save()
                    ctx.translate(cx, cy); ctx.rotate(rot); ctx.scale(s, -s); ctx.translate(-px, -py)
                    ctx.lineCap = "round"; ctx.lineJoin = "round"
                    var areas = root.mapData.areas          // optional layer of a richer map.json: [{c, p}]
                    if (areas) for (var a = 0; a < areas.length; a++) {
                        ctx.beginPath(); trace(ctx, areas[a].p, 0, areas[a].p.length); ctx.closePath()
                        ctx.fillStyle = areaColors[areas[a].c] || "#15171c"; ctx.fill()
                    }
                    var order = ["service", "residential", "unclassified", "tertiary_link", "tertiary", "secondary_link", "secondary",
                                 "primary_link", "primary", "trunk", "motorway"]
                    var labels = []
                    for (var o = 0; o < order.length; o++) {
                        var list = root.classes[order[o]]
                        if (!list) continue
                        var st = styles[order[o]]
                        ctx.beginPath()
                        for (var i = 0; i < list.length; i++) {
                            var b = list[i].b
                            if (b[2] < px - R || b[0] > px + R || b[3] < py - R || b[1] > py + R) continue
                            trace(ctx, list[i].p, 0, list[i].p.length)
                            if (list[i].name && labels.length < 6 && o >= 5) labels.push(list[i])   // optional road names
                        }
                        ctx.strokeStyle = st[0]; ctx.lineWidth = st[1] / s; ctx.stroke()
                    }
                    // route: travelled part dim, remaining part bright
                    var rp = root.routeFlat, vi = H.vertexAt(root.routeCum, root.lap.into) * 2 + 2
                    ctx.beginPath(); trace(ctx, rp, 0, vi); ctx.lineTo(px, py)
                    ctx.strokeStyle = Theme.accentDim.toString(); ctx.lineWidth = 9 / s; ctx.stroke()
                    ctx.beginPath(); ctx.moveTo(px, py)
                    for (var j = vi; j < rp.length; j += 2) ctx.lineTo(rp[j], rp[j + 1])
                    ctx.strokeStyle = Theme.accent.toString(); ctx.lineWidth = 9 / s; ctx.stroke()
                    ctx.restore()
                    ctx.font = Theme.tXs + "px sans-serif"; ctx.fillStyle = Theme.inkMuted.toString()
                    for (var n = 0; n < labels.length; n++) {
                        var lp = labels[n].p, mi = Math.floor(lp.length / 4) * 2, dx = lp[mi] - px, dy = lp[mi + 1] - py
                        var sx = cx + ((dx * Math.cos(-rot) - dy * Math.sin(-rot))) * s
                        var sy = cy - ((dx * Math.sin(-rot) + dy * Math.cos(-rot))) * s
                        if (sx > 0 && sx < width - 200 && sy > 40 && sy < height - 40) ctx.fillText(labels[n].name, sx, sy)
                    }
                    // position marker: blue disc, white ring, white arrow (arrow turns in north-up mode)
                    ctx.save(); ctx.translate(cx, cy); ctx.rotate(root.headingUp ? 0 : hr)
                    ctx.fillStyle = Theme.ink.toString(); ctx.beginPath(); ctx.arc(0, 0, 40, 0, Math.PI * 2); ctx.fill()
                    ctx.fillStyle = Theme.accent.toString(); ctx.beginPath(); ctx.arc(0, 0, 33, 0, Math.PI * 2); ctx.fill()
                    ctx.fillStyle = Theme.ink.toString(); ctx.beginPath()
                    ctx.moveTo(0, -20); ctx.lineTo(16, 16); ctx.lineTo(0, 7); ctx.lineTo(-16, 16); ctx.closePath(); ctx.fill()
                    ctx.restore()
                    corners(ctx)
                }
            }
            Card {   // search card with shortcuts (visual)
                x: Theme.s3 - 4; anchors.bottom: parent.bottom; anchors.bottomMargin: Theme.s3 - 4; width: 436; height: 132
                Icon { x: Theme.s3; y: 15; kind: "search"; size: Theme.iconSm; color: Theme.inkMuted; knock: Theme.overlay }
                Text { x: 80; y: 14; text: "검색"; color: Theme.inkMuted; font.pixelSize: Theme.tSm; font.family: Theme.fBody }
                Rectangle { y: 66; width: parent.width; height: 2; color: Theme.surface }
                Repeater {
                    model: ["home", "building", "star", "fuel", "P"]
                    Item { x: 75 + index * 71 - 22; y: 76; width: 44; height: 44
                        Icon { visible: modelData !== "P"; anchors.centerIn: parent; kind: modelData; size: Theme.iconLg
                            color: modelData === "building" ? Theme.inkMuted : Theme.ink; knock: Theme.overlay }
                        Text { visible: modelData === "P"; anchors.centerIn: parent; text: "P"; color: Theme.ink
                            font.pixelSize: Theme.tXl; font.family: Theme.fHead }
                    }
                }
            }
            Card {   // next turn and remaining distance, computed from the route polyline
                visible: root.nav.x !== undefined; x: Theme.s3 - 4; y: Theme.s3 - 4; width: 470; height: 116
                Canvas {
                    x: Theme.s3; y: 18; width: 64; height: 64
                    property real angle: root.turn ? Math.max(-150, Math.min(150, root.turn.angle)) : 0
                    onAngleChanged: requestPaint()
                    onPaint: {
                        var c = getContext("2d"); c.reset()
                        c.translate(32, 32); c.scale(64 / 48, 64 / 48); c.rotate(angle * Math.PI / 180)
                        c.fillStyle = Theme.ink.toString(); c.beginPath()
                        c.moveTo(0, -21); c.lineTo(15, -3); c.lineTo(5.5, -3); c.lineTo(5.5, 21); c.lineTo(-5.5, 21); c.lineTo(-5.5, -3); c.lineTo(-15, -3)
                        c.closePath(); c.fill()
                    }
                }
                Text { x: 108; y: 6; font.pixelSize: Theme.tLg; font.family: Theme.fHead; color: Theme.ink
                    text: root.turn ? H.formatDistance(root.turn.distance) : "직진" }
                Row { x: 108; y: 66; spacing: Theme.s1
                    Text { font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed; color: Theme.accent
                        text: root.turn ? (root.turn.direction === "right" ? "우회전" : "좌회전") : "전환 없음" }
                    Text { font.pixelSize: Theme.tXs; font.family: Theme.fBody; color: Theme.inkMuted
                        text: "· 남은 " + H.formatDistance(H.remainingDistance(root.routeLen, root.nav.distance)) } }
            }
            Card {   // background map workers (real, status.json)
                anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: Theme.s3 - 4 }
                height: 56; width: workersText.width + 2 * Theme.s3; radius: height / 2
                Text { id: workersText; anchors.centerIn: parent; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed
                    color: root.workers > 0 ? Theme.warn : Theme.inkMuted; text: "지도 작업자 " + root.workers + "개" }
            }
            Card {   // attribution, under the marker where the reference shows the road name
                x: 484; anchors.bottom: parent.bottom; anchors.bottomMargin: Theme.s2
                height: 48; width: attribution.width + 2 * Theme.s3; radius: height / 2
                Text { id: attribution; anchors.centerIn: parent; font.pixelSize: Theme.tXs; font.family: Theme.fBody; color: Theme.ink
                    text: "© OpenStreetMap contributors" }
            }
            Column {   // right-edge map buttons
                anchors { right: parent.right; rightMargin: Theme.s3 - 4; top: parent.top; topMargin: Theme.s3 - 4 } spacing: Theme.s2
                MapButton {
                    Column { anchors.centerIn: parent; spacing: 0
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "2D"; color: Theme.ink; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
                        Icon { anchors.horizontalCenter: parent.horizontalCenter; kind: "arrowUp"; size: Theme.iconXs; knock: Theme.overlay } }
                }
                MapButton {
                    onClicked: root.headingUp = !root.headingUp
                    Column { anchors.centerIn: parent; spacing: 0
                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: root.headingUp ? "AUTO" : "북향"; color: Theme.ink
                            font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
                        Icon { anchors.horizontalCenter: parent.horizontalCenter; kind: "mapfold"; size: Theme.iconXs; knock: Theme.overlay } }
                }
            }
            Column {
                anchors { right: parent.right; rightMargin: Theme.s3 - 4; bottom: parent.bottom; bottomMargin: Theme.s3 - 4 } spacing: Theme.s2
                MapButton { Icon { anchors.centerIn: parent; kind: "traffic"; size: Theme.iconLg; knock: Theme.overlay } }
                MapButton { Icon { anchors.centerIn: parent; kind: "gear"; size: Theme.iconLg; knock: Theme.overlay } }
            }
        }
    }

    // =============== dock ===============
    Item {
        id: dock; y: root.height - Theme.dockH; width: root.width; height: Theme.dockH
        readonly property int mid: Theme.dockH / 2
        Icon { x: 68 - width / 2; y: dock.mid - height / 2; kind: "car"; size: Theme.iconLg + 8 }
        Icon { x: 139 - width / 2; y: dock.mid - height / 2; kind: "chevL"; size: Theme.iconSm; color: "#2d7cff" }
        Readout { x: 214 - width / 2; y: 2; whole: "23"; fraction: ".5°"; label: "A/C"; labelColor: Theme.good }
        Icon { x: 287 - width / 2; y: dock.mid - height / 2; kind: "chevR"; size: Theme.iconSm; color: "#e5484d" }
        Icon { x: 355 - width / 2; y: dock.mid - height / 2; kind: "defrostF"; size: Theme.iconLg + 8 }
        Icon { x: 451 - width / 2; y: dock.mid - height / 2; kind: "defrostR"; size: Theme.iconLg + 8 }
        Icon { x: 562 - width / 2; y: dock.mid - height / 2; kind: "seatFan"; size: Theme.iconLg + 8 }
        DockTile { x: 661 - width / 2; y: dock.mid - height / 2; kind: "fan"; c1: "#7c7c7c"; c2: "#3c3c3c" }
        DockTile { x: 757 - width / 2; y: dock.mid - height / 2; kind: "music"; c1: "#9a6bff"; c2: "#5a2fd8" }
        DockTile { x: 853 - width / 2; y: dock.mid - height / 2; kind: "phone"; c1: "#3ed7a6"; c2: "#1f9d7c" }
        DockTile { x: 949 - width / 2; y: dock.mid - height / 2; kind: "navi"; c1: "#3aa4ff"; c2: "#1f63e0"
            active: root.page === "map"; onClicked: root.page = "map" }
        DockTile { x: 1045 - width / 2; y: dock.mid - height / 2; kind: "apps"; c1: "#00000000"; c2: "#00000000"
            active: root.page === "home"; onClicked: root.page = "home" }
        Rectangle { x: 1093; y: 14; width: 2; height: 60; color: Theme.overlayLine }
        DockTile { x: 1148 - width / 2; y: dock.mid - height / 2; kind: "manual"; c1: "#aeb5c0"; c2: "#6e7580" }
        DockTile { x: 1244 - width / 2; y: dock.mid - height / 2; kind: "radio"; c1: "#ff66ae"; c2: "#df2a82" }
        Rectangle { x: 1300; y: 14; width: 2; height: 60; color: Theme.overlayLine }
        Item { x: 1354 - width / 2; width: 90; height: parent.height
            Icon { x: 4; y: 10; kind: "fan"; size: Theme.iconSm; knock: Theme.bg }
            Text { x: 44; y: 4; text: "3"; color: Theme.ink; font.pixelSize: Theme.tLg; font.family: Theme.fHead }
            Text { anchors.horizontalCenter: parent.horizontalCenter; y: 54; text: "AUTO"; color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed } }
        Icon { x: 1449 - width / 2; y: dock.mid - height / 2; kind: "seatVent"; size: Theme.iconLg + 8 }
        Icon { x: 1540 - width / 2; y: dock.mid - height / 2; kind: "airflow"; size: Theme.iconLg + 8 }
        Icon { x: 1642 - width / 2; y: dock.mid - height / 2; kind: "recirc"; size: Theme.iconLg + 8 }
        Icon { x: 1727 - width / 2; y: dock.mid - height / 2; kind: "chevL"; size: Theme.iconSm; color: "#2d7cff" }
        Readout { x: 1800 - width / 2; y: 2; whole: "23"; fraction: ".5°"; label: "SYNC"; labelColor: Theme.good }
        Icon { x: 1878 - width / 2; y: dock.mid - height / 2; kind: "chevR"; size: Theme.iconSm; color: "#e5484d" }
    }
}
