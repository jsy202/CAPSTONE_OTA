import QtQuick 2.15
import "."
import "ivi.js" as H
import "geo.js" as Geo

// Bottom strip of the Pi2 monitoring screen (loaded by dashboard/cockpit/Cockpit.qml at 1920x600, scaled x1.125 to 2160x675).
// The digital cluster above already shows speed, rpm and gear, so none of them is repeated here.
// Layout: status bar / vehicle column (25%) + stage / dock. The stage is the map (builds with a map, see RoadMap.qml)
// or a media player panel (builds without a map). Roads/route (c) OpenStreetMap contributors (ODbL), drawn locally.
// Real data: clock, status.json (version, CAN link, map feature, map workers), route figures computed from map.json
// and nav.json. Everything else is visual. All sizes and colours come from Theme.qml.
// CPU rule: static items paint once; only the clock, the player progress and the map overlay (<= 5 fps) change.
Item {
    id: root
    width: 1920; height: 600

    // ---- interface used by the cockpit ----
    property string runDir: "file:///run/capstone-ivi/"
    property var vstatus: ({})
    property var nav: ({})
    readonly property bool mapActive: vstatus.map_active === true

    // ---- state ----
    property string page: "home"          // "home" (media player) | "map"
    property bool headingUp: true         // AUTO button: heading-up vs north-up map
    property bool playing: true
    property int track: 0
    property real elapsed: 0
    property real volume: 0.6
    readonly property var tracks: [
        { title: "Music 1", artist: "Artist 1", length: 214, c1: "#9a6bff", c2: "#4a28b8" },
        { title: "Music 2", artist: "Artist 2", length: 187, c1: "#4aa8ff", c2: "#2a3fb0" },
        { title: "Music 3", artist: "Artist 3", length: 241, c1: "#c26bff", c2: "#5b2aa8" },
        { title: "Music 4", artist: "Artist 4", length: 198, c1: "#7a7bff", c2: "#2f2a9a" },
        { title: "Music 5", artist: "Artist 5", length: 226, c1: "#b07cff", c2: "#3d2a9f" } ]

    readonly property bool mapEnabled: vstatus.map_enabled === true
    readonly property bool canLive: vstatus.can === true
    readonly property int workers: vstatus.workers > 0 ? vstatus.workers : 0
    readonly property string version: vstatus.version ? vstatus.version : "…"
    readonly property var current: tracks[track]
    readonly property var info: roadMap.info

    // The page is driven only by taps: V3 starts on the player like V2, the navigation tile opens the map.
    // The map flag file (runDir + "map_active") is how the tap reaches ivi.py: "1" = map open, "0" = closed.
    property bool flagWriteOk: false      // true after the last flag write succeeded (read by tests)
    function setMapFlag(on) {     // needs QML_XHR_ALLOW_FILE_WRITE=1 in the qmlscene environment; failures are swallowed
        var text = on ? "1" : "0"
        flagWriteOk = false
        try {
            var x = new XMLHttpRequest()
            // A local-file PUT reports status 0 even when it failed, so read the file back to know it really took.
            x.onreadystatechange = function () {
                if (x.readyState !== 4) return
                readText(runDir + "map_active", function (t) { flagWriteOk = t === text })
            }
            x.open("PUT", runDir + "map_active")
            x.send(text)
        } catch (e) { flagWriteOk = false }
    }
    function readText(url, done) {
        var r = new XMLHttpRequest()
        r.onreadystatechange = function () { if (r.readyState === 4) { try { done(r.responseText) } catch (e) { } } }
        r.open("GET", url); r.send()
    }
    function openMap() {
        if (!mapEnabled) { page = "map"; return }     // V2: shows the 'no map in this version' notice, writes nothing
        page = "map"; setMapFlag(true)
    }
    function goHome() {
        var wasMap = page === "map"
        page = "home"
        if (mapEnabled && wasMap) setMapFlag(false)
    }

    // ---- data plumbing (a sample identical to the last one is dropped, so idle data costs no bindings/repaints) ----
    function readJson(url, done) {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState === 4) { try { done(JSON.parse(x.responseText)) } catch (e) { } }
        }
        x.open("GET", url); x.send()
    }
    function stepTrack(dir) { track = H.stepTrack(track, tracks.length, dir); elapsed = 0 }
    Component.onCompleted: readJson(Qt.resolvedUrl("../map.json"), function (d) { roadMap.mapData = d })
    Timer { interval: 200; running: true; repeat: true
        onTriggered: readJson(root.runDir + "nav.json", function (d) { if (!H.sameSample(d, root.nav)) root.nav = d }) }
    Timer { interval: 1000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: {
            readJson(root.runDir + "status.json", function (d) { if (!H.sameSample(d, root.vstatus)) root.vstatus = d })
            clockLabel.text = H.clockText(new Date())
        } }
    Timer { interval: 1000; running: root.playing && root.page === "home"; repeat: true
        onTriggered: {
            var t = H.playerTick(root.elapsed, root.current.length, true)
            if (t.ended) root.stepTrack(1); else root.elapsed = t.elapsed
        } }

    FontLoader { source: "fonts/SpaceGrotesk-Bold.ttf" }
    FontLoader { source: "fonts/DMSans-Regular.ttf" }
    FontLoader { source: "fonts/DMSans-Medium.ttf" }

    Rectangle { anchors.fill: parent; color: Theme.bg }

    // =============== status bar ===============
    Item {
        id: topBar; width: parent.width; height: Theme.topH
        readonly property int mid: height / 2

        Row {   // segmented strip along the top of the vehicle column
            x: Theme.s2; y: 0; spacing: 6
            Repeater { model: 7; Rectangle { width: (Theme.leftW - 2 * Theme.s2 - 6 * 6) / 7; height: 4; color: Theme.segment } }
        }
        Row {   // range (visual)
            x: Theme.s2; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 4; spacing: Theme.s1
            Text { anchors.verticalCenter: parent.verticalCenter; text: "514km"; color: Theme.ink; font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
            Icon { anchors.verticalCenter: parent.verticalCenter; kind: "fuel"; size: Theme.iconSm }
            Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 52; height: 28; radius: 6; color: Theme.segment
                Rectangle { width: 40; height: parent.height; radius: 6; color: Theme.ink } }
        }

        Row {   // search and notifications (visual), aligned with the stage edge below
            x: Theme.stageX + Theme.s1; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 2; spacing: Theme.s4
            Icon { kind: "search"; size: Theme.iconLg }
            Icon { kind: "bell"; size: Theme.iconLg }
        }

        Row {   // installed version (real, status.json)
            x: 760; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 2; spacing: Theme.s3
            Rectangle { height: 44; width: badgeText.width + 40; radius: 22; color: Theme.badge; anchors.verticalCenter: parent.verticalCenter
                Text { id: badgeText; anchors.centerIn: parent; text: "버전 " + root.version; color: Theme.inkOnBadge
                    font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed } }
        }

        Row {   // network, volume, bluetooth, profile (visual) and the real clock
            anchors { right: parent.right; rightMargin: Theme.s2; verticalCenter: parent.verticalCenter; verticalCenterOffset: 2 }
            spacing: Theme.s3
            Icon { anchors.verticalCenter: parent.verticalCenter; kind: "lte"; size: Theme.iconLg }
            Icon { anchors.verticalCenter: parent.verticalCenter; kind: "volume"; size: Theme.iconLg }
            Icon { anchors.verticalCenter: parent.verticalCenter; kind: "bluetooth"; size: Theme.iconLg }
            Icon { anchors.verticalCenter: parent.verticalCenter; kind: "person"; size: 48 }
            Text { id: clockLabel; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: -2; color: Theme.ink
                font.pixelSize: Theme.tMd; font.family: Theme.fHead }
        }
    }

    // =============== vehicle column (visual): chips, car, trip row ===============
    Item {
        id: leftCol; y: Theme.topH; x: Theme.s2; width: Theme.leftW - 2 * Theme.s2; height: Theme.stageH
        Row {
            anchors.horizontalCenter: parent.horizontalCenter; y: 36; spacing: Theme.s3
            Rectangle { width: 64; height: 64; radius: 32; color: Theme.accent
                Icon { anchors.centerIn: parent; kind: "lane"; size: Theme.iconLg; color: Theme.inkOnAccent } }
            Rectangle { width: 64; height: 64; radius: 32; color: Theme.overlay
                Icon { anchors.centerIn: parent; kind: "lock"; size: Theme.iconLg; knock: Theme.overlay } }
        }
        Image { anchors.horizontalCenter: parent.horizontalCenter; y: 142; width: 448; height: Math.round(448 * 323 / 685)
            source: "assets/car.png"; smooth: true }
        Item {   // trip row: odometer, expander, outside temperature on one baseline
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; bottomMargin: Theme.s2 } height: 40
            Text { anchors { left: parent.left; verticalCenter: parent.verticalCenter } text: "1576km"; color: Theme.ink
                font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
            Icon { anchors.centerIn: parent; kind: "chevUp"; size: Theme.iconSm; color: Theme.inkMuted }
            Text { anchors { right: parent.right; verticalCenter: parent.verticalCenter } text: "25°C"; color: Theme.ink
                font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
        }
    }

    // =============== stage: map (V3) or media player (V2) ===============
    Item {
        id: stage; x: Theme.stageX; y: Theme.topH; width: Theme.stageW; height: Theme.stageH

        // ---- media player (visual only, no audio backend) ----
        Rectangle {
            anchors.fill: parent; radius: Theme.rPanel; color: Theme.surface; visible: root.page === "home"
            readonly property int pad: Theme.s4
            Rectangle {   // generated cover art
                x: parent.pad; y: parent.pad; width: parent.height - 2 * parent.pad; height: width; radius: Theme.rPanel; clip: true
                gradient: Gradient {
                    GradientStop { position: 0.0; color: root.current.c1 }
                    GradientStop { position: 1.0; color: root.current.c2 } }
                Rectangle { width: parent.width * 0.8; height: width; radius: width / 2; x: parent.width * 0.25; y: parent.height * 0.28; color: root.current.c2; opacity: 0.55 }
                Rectangle { width: parent.width * 0.5; height: width; radius: width / 2; x: parent.width * 0.11; y: parent.width * 0.11; color: "transparent"; border.width: 4; border.color: Theme.ink; opacity: 0.85 }
                Rectangle { width: parent.width * 0.17; height: width; radius: width / 2; x: parent.width * 0.275; y: parent.width * 0.275; color: Theme.ink; opacity: 0.9 }
            }
            Item {   // title, progress, transport, volume
                x: 440; y: parent.pad; width: 520; height: parent.height - 2 * parent.pad
                Text { width: parent.width; elide: Text.ElideRight; text: root.current.title; color: Theme.ink
                    font.pixelSize: Theme.tXl; font.family: Theme.fHead }
                Text { y: 66; width: parent.width; elide: Text.ElideRight; text: root.current.artist; color: Theme.inkMuted
                    font.pixelSize: Theme.tSm; font.family: Theme.fBody }
                Rectangle {   // progress
                    id: bar; y: 150; width: parent.width; height: 8; radius: 4; color: Theme.overlayLine
                    Rectangle { width: parent.width * H.progressFraction(root.elapsed, root.current.length); height: parent.height; radius: 4; color: Theme.media }
                    MouseArea { anchors { fill: parent; topMargin: -16; bottomMargin: -16 }
                        onClicked: root.elapsed = Math.round(mouse.x / bar.width * root.current.length) }
                }
                Text { y: 170; text: H.formatTrackTime(root.elapsed); color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                Text { y: 170; anchors.right: parent.right; text: H.formatTrackTime(root.current.length); color: Theme.inkMuted
                    font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                Item {   // transport: prev, play/pause, next on one centre line
                    y: 216; width: parent.width; height: 96
                    Icon { x: 80 - width / 2; anchors.verticalCenter: parent.verticalCenter; kind: "prev"; size: Theme.iconLg
                        MouseArea { anchors { fill: parent; margins: -16 } onClicked: root.stepTrack(-1) } }
                    Rectangle { x: parent.width / 2 - 48; width: 96; height: 96; radius: 48
                        gradient: Gradient { GradientStop { position: 0.0; color: "#9a6bff" } GradientStop { position: 1.0; color: "#5a2fd8" } }
                        Icon { anchors.centerIn: parent; kind: root.playing ? "pause" : "play"; size: Theme.iconLg; knock: "#5a2fd8" }
                        MouseArea { anchors.fill: parent; onClicked: root.playing = !root.playing } }
                    Icon { x: parent.width - 80 - width / 2; anchors.verticalCenter: parent.verticalCenter; kind: "next"; size: Theme.iconLg
                        MouseArea { anchors { fill: parent; margins: -16 } onClicked: root.stepTrack(1) } }
                }
                Row {   // volume
                    y: parent.height - height; spacing: Theme.s2; height: 36
                    Icon { anchors.verticalCenter: parent.verticalCenter; kind: "volume"; size: Theme.iconSm; color: Theme.inkMuted }
                    Rectangle { id: vol; anchors.verticalCenter: parent.verticalCenter; width: parent.parent.width - 36 - Theme.s2; height: 8; radius: 4; color: Theme.overlayLine
                        Rectangle { width: parent.width * root.volume; height: parent.height; radius: 4; color: Theme.inkMuted }
                        MouseArea { anchors { fill: parent; topMargin: -14; bottomMargin: -14 }
                            onPressed: root.volume = Math.max(0, Math.min(1, mouse.x / vol.width))
                            onPositionChanged: if (pressed) root.volume = Math.max(0, Math.min(1, mouse.x / vol.width)) } }
                }
            }
            Rectangle { x: 992; y: parent.pad; width: 2; height: parent.height - 2 * parent.pad; color: Theme.overlayLine }
            Item {   // next up
                x: 1026; y: parent.pad; width: parent.width - 1026 - parent.pad; height: parent.height - 2 * parent.pad
                Text { text: "다음 곡"; color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
                Repeater {
                    model: 3
                    Column { y: 56 + index * 104; width: parent.width
                        readonly property var t: root.tracks[(root.track + 1 + index) % root.tracks.length]
                        Text { width: parent.width; elide: Text.ElideRight; text: parent.t.title; color: Theme.ink
                            font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
                        Text { width: parent.width; elide: Text.ElideRight; text: parent.t.artist; color: Theme.inkMuted
                            font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                    }
                }
            }
        }

        // ---- map page of a build without a map ----
        Rectangle {
            anchors.fill: parent; radius: Theme.rPanel; color: Theme.surface; visible: root.page === "map" && !root.mapEnabled
            Text { anchors.centerIn: parent; width: parent.width - 160; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: "이 버전에는 내비게이션 지도가 없습니다"; color: Theme.ink; font.pixelSize: Theme.tXl; font.family: Theme.fHead }
        }

        // ---- map page ----
        Item {
            anchors.fill: parent; visible: root.page === "map" && root.mapEnabled
            RoadMap { id: roadMap; anchors.fill: parent; nav: root.nav; headingUp: root.headingUp }
            Card {   // next turn and remaining distance, computed from the route polyline (geo.js)
                visible: root.info !== null; x: Theme.s2; y: Theme.s2; width: 470; height: 116
                Canvas {
                    x: Theme.s3; y: 26; width: 64; height: 64
                    property real angle: root.info && root.info.turn ? Math.max(-150, Math.min(150, root.info.turn.angle)) : 0
                    onAngleChanged: requestPaint()
                    onPaint: {
                        var c = getContext("2d"); c.reset()
                        c.translate(32, 32); c.scale(64 / 48, 64 / 48); c.rotate(angle * Math.PI / 180)
                        c.fillStyle = Theme.ink.toString(); c.beginPath()
                        c.moveTo(0, -21); c.lineTo(15, -3); c.lineTo(5.5, -3); c.lineTo(5.5, 21); c.lineTo(-5.5, 21); c.lineTo(-5.5, -3); c.lineTo(-15, -3)
                        c.closePath(); c.fill()
                    }
                }
                Column { x: 112; anchors.verticalCenter: parent.verticalCenter; spacing: 0
                    Text { font.pixelSize: Theme.tLg; font.family: Theme.fHead; color: Theme.ink
                        text: root.info && root.info.turn ? Geo.formatDistance(root.info.turn.distance) : "직진" }
                    Row { spacing: Theme.s1
                        Text { font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed; color: Theme.accent
                            text: root.info && root.info.turn ? (root.info.turn.angle > 0 ? "우회전" : "좌회전") : "전환 없음" }
                        Text { font.pixelSize: Theme.tXs; font.family: Theme.fBody; color: Theme.inkMuted
                            text: root.info ? "· 남은 " + Geo.formatDistance(root.info.remaining) : "" } } }
            }
            Card {   // background map workers (real, status.json)
                anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: Theme.s2 }
                height: 56; width: workersText.width + 2 * Theme.s3; radius: height / 2
                Text { id: workersText; anchors.centerIn: parent; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed
                    color: root.workers > 0 ? Theme.warn : Theme.inkMuted; text: "지도 작업자 " + root.workers + "개" }
            }
            Card {   // search card with five shortcuts (visual)
                x: Theme.s2; anchors.bottom: parent.bottom; anchors.bottomMargin: Theme.s2; width: 460; height: 132
                Icon { x: Theme.s3; y: 15; kind: "search"; size: Theme.iconSm; color: Theme.inkMuted; knock: Theme.overlay }
                Text { x: 80; y: 14; text: "검색"; color: Theme.inkMuted; font.pixelSize: Theme.tSm; font.family: Theme.fBody }
                Rectangle { y: 66; width: parent.width; height: 2; color: Theme.surface }
                Repeater {
                    model: ["home", "building", "star", "fuel", "P"]
                    Item { x: index * parent.width / 5; y: 68; width: parent.width / 5; height: 64
                        Icon { visible: modelData !== "P"; anchors.centerIn: parent; kind: modelData; size: Theme.iconLg
                            color: modelData === "building" ? Theme.inkMuted : Theme.ink; knock: Theme.overlay }
                        Text { visible: modelData === "P"; anchors.centerIn: parent; text: "P"; color: Theme.ink
                            font.pixelSize: Theme.tXl; font.family: Theme.fHead }
                    }
                }
            }
            Column {   // right-edge map buttons, top pair
                anchors { right: parent.right; rightMargin: Theme.s2; top: parent.top; topMargin: Theme.s2 } spacing: Theme.s2
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
            Column {   // bottom pair
                anchors { right: parent.right; rightMargin: Theme.s2; bottom: parent.bottom; bottomMargin: Theme.s2 } spacing: Theme.s2
                MapButton { Icon { anchors.centerIn: parent; kind: "traffic"; size: Theme.iconLg; knock: Theme.overlay } }
                MapButton { Icon { anchors.centerIn: parent; kind: "gear"; size: Theme.iconLg; knock: Theme.overlay } }
            }
        }
    }

    // =============== dock: equal 64px tiles on one 88px pitch, content centred in each slot ===============
    Item {
        id: dock; y: root.height - Theme.dockH; width: root.width; height: Theme.dockH
        Row {
            anchors.horizontalCenter: parent.horizontalCenter; height: parent.height
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "car"; size: Theme.iconXl } }
            DockSlot { w: 0.7; Icon { anchors.centerIn: parent; kind: "chevL"; size: Theme.iconSm; color: "#2d7cff" } }
            DockSlot { w: 1.3; Readout { anchors.centerIn: parent; whole: "23"; fraction: ".5°"; label: "A/C"; labelColor: Theme.good } }
            DockSlot { w: 0.7; Icon { anchors.centerIn: parent; kind: "chevR"; size: Theme.iconSm; color: "#e5484d" } }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "defrostF"; size: Theme.iconXl } }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "defrostR"; size: Theme.iconXl } }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "seatFan"; size: Theme.iconXl } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "fan"; c1: "#7e7e7e"; c2: "#3c3c3c" } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "music"; c1: "#9a6bff"; c2: "#5a2fd8" } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "phone"; c1: "#3ed7a6"; c2: "#1f9d7c" } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "navi"; c1: "#3aa4ff"; c2: "#1f63e0"
                active: root.page === "map"; onClicked: root.openMap() } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "apps"; c1: "#00000000"; c2: "#00000000"
                active: root.page === "home"; onClicked: root.goHome() } }
            DockSlot { w: 0.25; Rectangle { anchors.centerIn: parent; width: 2; height: 56; color: Theme.overlayLine } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "manual"; c1: "#aeb5c0"; c2: "#6e7580" } }
            DockSlot { w: 1; DockTile { anchors.centerIn: parent; kind: "radio"; c1: "#ff66ae"; c2: "#df2a82" } }
            DockSlot { w: 0.25; Rectangle { anchors.centerIn: parent; width: 2; height: 56; color: Theme.overlayLine } }
            DockSlot { w: 1.1
                Column { anchors.centerIn: parent; spacing: 0
                    Row { anchors.horizontalCenter: parent.horizontalCenter; spacing: 6
                        Icon { anchors.verticalCenter: parent.verticalCenter; kind: "fan"; size: Theme.iconSm; knock: Theme.bg }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: "3"; color: Theme.ink; font.pixelSize: Theme.tLg; font.family: Theme.fHead } }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "AUTO"; color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed } }
            }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "seatVent"; size: Theme.iconXl } }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "airflow"; size: Theme.iconXl } }
            DockSlot { w: 1; Icon { anchors.centerIn: parent; kind: "recirc"; size: Theme.iconXl } }
            DockSlot { w: 0.7; Icon { anchors.centerIn: parent; kind: "chevL"; size: Theme.iconSm; color: "#2d7cff" } }
            DockSlot { w: 1.3; Readout { anchors.centerIn: parent; whole: "23"; fraction: ".5°"; label: "SYNC"; labelColor: Theme.good } }
            DockSlot { w: 0.7; Icon { anchors.centerIn: parent; kind: "chevR"; size: Theme.iconSm; color: "#e5484d" } }
        }
    }
}
