import QtQuick 2.15
import "player.js" as Player

// Visual-only music player panel (no QtMultimedia). Replaces the map area
// when the head unit build has no map. Size it from the parent via
// width/height; the design size is about 1450 x 540.
Rectangle {
    id: root

    // ---- public API -------------------------------------------------
    property bool playing: true
    property int trackIndex: 0
    property real position: 0          // elapsed seconds in the current track
    property real volume: 0.6          // 0..1
    readonly property int trackCount: tracks.count
    readonly property real duration: tracks.count > 0 ? tracks.get(trackIndex).duration : 0
    readonly property string title: tracks.count > 0 ? tracks.get(trackIndex).title : ""
    readonly property string artist: tracks.count > 0 ? tracks.get(trackIndex).artist : ""
    signal trackChanged(int index)

    function togglePlaying() { playing = !playing }
    function next() { selectTrack(trackIndex + 1) }
    function previous() { selectTrack(trackIndex - 1) }
    function selectTrack(i) {
        trackIndex = Player.wrapIndex(i, tracks.count)
        position = 0
        trackChanged(trackIndex)
    }
    function seek(ratio) { position = Player.clamp01(ratio) * duration }
    function setVolume(ratio) { volume = Player.clamp01(ratio) }

    // ---- palette and spacing (reference head unit) ------------------
    readonly property color cBg: "#15161a"
    readonly property color cCard: "#25262b"
    readonly property color cCardHi: "#313236"
    readonly property color cText: "#f4f5f7"
    readonly property color cSub: "#aeb1ba"      // 8.5:1 on cBg
    readonly property color cAccent: "#1fa6f9"
    readonly property color cPurpleA: "#8745f9"
    readonly property color cPurpleB: "#6524dd"
    readonly property color cTrack: "#3a3b42"
    readonly property int sp1: 8
    readonly property int sp2: 16
    readonly property int sp3: 24
    readonly property int sp4: 32
    readonly property int rad: 16                 // cards; inner items use rad / 2

    color: cBg
    radius: rad
    clip: true

    ListModel {
        id: tracks
        // hueA/hueB are colours for the generated cover
        ListElement { title: "새벽 고속도로"; artist: "아틀라스 퀸텟"; duration: 214; ca: "#8745f9"; cb: "#1fa6f9" }
        ListElement { title: "Midnight Overpass"; artist: "Lumen Harbor"; duration: 187; ca: "#5b2bd0"; cb: "#e04fa0" }
        ListElement { title: "푸른 터널의 끝"; artist: "노바 스튜디오"; duration: 242; ca: "#1fa6f9"; cb: "#4b2fd6" }
        ListElement { title: "Quiet Lanes"; artist: "Paper Compass"; duration: 198; ca: "#6524dd"; cb: "#2fd0c0" }
        ListElement { title: "도시의 불빛"; artist: "하늘정원"; duration: 226; ca: "#b04cf0"; cb: "#1f6ff9" }
    }

    // Collects durations for the pure-JS advance step.
    function durations() {
        var d = []
        for (var i = 0; i < tracks.count; i++) d.push(tracks.get(i).duration)
        return d
    }

    Timer {
        interval: 250
        repeat: true
        running: root.playing && root.visible
        onTriggered: {
            var s = Player.advance({index: root.trackIndex, position: root.position},
                                   interval / 1000, root.durations())
            if (s.index !== root.trackIndex) root.selectTrack(s.index)
            else root.position = s.position
        }
    }

    // One small glyph painter shared by all buttons (no Icon.qml dependency).
    component Glyph: Canvas {
        id: g
        property string kind: "play"
        property color fg: "#ffffff"
        onKindChanged: requestPaint()
        onFgChanged: requestPaint()
        onWidthChanged: requestPaint()
        onPaint: {
            var c = getContext("2d"), w = width, h = height
            c.reset()
            c.fillStyle = fg
            c.strokeStyle = fg
            c.lineJoin = "round"
            c.lineWidth = Math.max(2, w * 0.08)
            function tri(x0, x1) {   // right-pointing triangle between x0 and x1
                c.beginPath(); c.moveTo(x0, h * 0.12); c.lineTo(x1, h / 2); c.lineTo(x0, h * 0.88)
                c.closePath(); c.fill(); c.stroke()
            }
            if (kind === "play") {
                tri(w * 0.22, w * 0.84)
            } else if (kind === "pause") {
                c.fillRect(w * 0.2, h * 0.12, w * 0.22, h * 0.76)
                c.fillRect(w * 0.58, h * 0.12, w * 0.22, h * 0.76)
            } else if (kind === "next") {
                tri(w * 0.12, w * 0.74)
                c.fillRect(w * 0.76, h * 0.12, w * 0.12, h * 0.76)
            } else if (kind === "prev") {
                c.save(); c.translate(w, 0); c.scale(-1, 1)
                tri(w * 0.12, w * 0.74)
                c.fillRect(w * 0.76, h * 0.12, w * 0.12, h * 0.76)
                c.restore()
            } else if (kind === "speaker") {
                c.beginPath()
                c.moveTo(w * 0.08, h * 0.38); c.lineTo(w * 0.28, h * 0.38); c.lineTo(w * 0.5, h * 0.16)
                c.lineTo(w * 0.5, h * 0.84); c.lineTo(w * 0.28, h * 0.62); c.lineTo(w * 0.08, h * 0.62)
                c.closePath(); c.fill()
                c.lineCap = "round"
                c.beginPath(); c.arc(w * 0.5, h / 2, w * 0.2, -0.9, 0.9); c.stroke()
                c.beginPath(); c.arc(w * 0.5, h / 2, w * 0.36, -0.9, 0.9); c.stroke()
            }
        }
    }

    // Round transport button; pressed state darkens, tap area >= 72 px.
    component RoundButton: Rectangle {
        id: b
        property string glyph: "play"
        property bool primary: false
        property real glyphSize: 32
        signal clicked()
        width: primary ? 88 : 72
        height: width
        radius: width / 2
        color: tap.pressed ? root.cCardHi : root.cCard
        // Purple media-tile gradient on the primary button only.
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            visible: b.primary
            opacity: tap.pressed ? 0.8 : 1
            gradient: Gradient {
                GradientStop { position: 0; color: root.cPurpleA }
                GradientStop { position: 1; color: root.cPurpleB }
            }
        }
        Glyph {
            anchors.centerIn: parent
            width: b.glyphSize; height: b.glyphSize
            kind: b.glyph
            fg: root.cText
        }
        MouseArea { id: tap; anchors.fill: parent; onClicked: b.clicked() }
    }

    // Horizontal slider/progress bar with a generous touch area.
    component Bar: Item {
        id: bar
        property real value: 0          // 0..1
        property color fill: root.cAccent
        property bool knob: true
        signal moved(real ratio)
        height: 48
        Rectangle {
            id: rail
            width: parent.width; height: 12; radius: 6
            anchors.verticalCenter: parent.verticalCenter
            color: root.cTrack
            Rectangle {
                width: Math.max(rail.height, parent.width * bar.value)
                height: parent.height; radius: 6
                color: bar.fill
            }
        }
        Rectangle {
            visible: bar.knob
            width: 28; height: 28; radius: 14
            color: root.cText
            anchors.verticalCenter: parent.verticalCenter
            x: Math.max(0, Math.min(1, bar.value)) * (bar.width - width)
        }
        MouseArea {
            anchors.fill: parent
            onPressed: bar.moved(Player.ratioFromX(mouse.x, 0, bar.width))
            onPositionChanged: if (pressed) bar.moved(Player.ratioFromX(mouse.x, 0, bar.width))
        }
    }

    // ---- album art ----------------------------------------------------
    Rectangle {
        id: art
        x: root.sp4; y: root.sp4
        width: root.height - 2 * root.sp4
        height: width
        radius: root.rad
        clip: true
        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0; color: tracks.get(root.trackIndex).ca }
            GradientStop { position: 1; color: tracks.get(root.trackIndex).cb }
        }
        Canvas {
            id: cover
            anchors.fill: parent
            property int seed: root.trackIndex
            onSeedChanged: requestPaint()
            onWidthChanged: requestPaint()
            onPaint: {
                var c = getContext("2d"), w = width, h = height
                c.reset()
                // Concentric rings around a drifting centre, differs per track.
                var cx = w * (0.3 + 0.12 * seed), cy = h * (0.72 - 0.08 * seed)
                for (var i = 1; i <= 7; i++) {
                    c.beginPath()
                    c.arc(cx, cy, i * w * 0.11, 0, Math.PI * 2)
                    c.strokeStyle = Qt.rgba(1, 1, 1, 0.06 + 0.02 * (7 - i))
                    c.lineWidth = 3
                    c.stroke()
                }
                // Soft horizon band and a sun-like disc.
                c.fillStyle = Qt.rgba(0, 0, 0, 0.22)
                c.fillRect(0, h * 0.74, w, h * 0.26)
                c.beginPath()
                c.arc(w * 0.66, h * 0.42, w * 0.17, 0, Math.PI * 2)
                c.fillStyle = Qt.rgba(1, 1, 1, 0.9)
                c.fill()
                c.beginPath()
                c.arc(w * 0.66, h * 0.42, w * 0.17, 0, Math.PI * 2)
                c.clip()
                c.fillStyle = Qt.rgba(0, 0, 0, 0.16)
                for (var k = 0; k < 4; k++) c.fillRect(w * 0.4, h * (0.42 + 0.03 * k) , w * 0.5, h * 0.012)
            }
        }
    }

    // ---- right-hand content --------------------------------------------
    Item {
        id: content
        x: art.x + art.width + root.sp4
        y: root.sp4
        width: root.width - x - root.sp4
        height: root.height - 2 * root.sp4

        Text {
            id: titleText
            height: 52
            verticalAlignment: Text.AlignVCenter
            width: parent.width
            text: root.title
            color: root.cText
            font.pixelSize: 44
            font.bold: true
            elide: Text.ElideRight
        }
        Text {
            id: artistText
            height: 40
            verticalAlignment: Text.AlignVCenter
            y: titleText.height
            width: parent.width
            text: root.artist
            color: root.cSub
            font.pixelSize: 30
            elide: Text.ElideRight
        }

        // progress + times
        Bar {
            id: progress
            y: artistText.y + artistText.height + root.sp1
            width: parent.width
            value: root.duration > 0 ? root.position / root.duration : 0
            onMoved: root.seek(ratio)
        }
        Text {
            y: progress.y + progress.height
            text: Player.formatTime(root.position)
            color: root.cSub
            font.pixelSize: 28
        }
        Text {
            y: progress.y + progress.height
            anchors.right: parent.right
            text: Player.formatTime(root.duration)
            color: root.cSub
            font.pixelSize: 28
        }

        // transport row + volume
        Row {
            id: transport
            y: progress.y + progress.height + 44
            spacing: root.sp2
            RoundButton { glyph: "prev"; anchors.verticalCenter: parent.verticalCenter; onClicked: root.previous() }
            RoundButton {
                primary: true; glyphSize: 40
                glyph: root.playing ? "pause" : "play"
                anchors.verticalCenter: parent.verticalCenter
                onClicked: root.togglePlaying()
            }
            RoundButton { glyph: "next"; anchors.verticalCenter: parent.verticalCenter; onClicked: root.next() }
        }
        Glyph {
            id: speaker
            kind: "speaker"; fg: root.cSub
            width: 40; height: 40
            x: parent.width - volBar.width - width - root.sp2
            anchors.verticalCenter: transport.verticalCenter
        }
        Bar {
            id: volBar
            width: 260
            anchors.right: parent.right
            anchors.verticalCenter: transport.verticalCenter
            value: root.volume
            fill: root.cPurpleA
            onMoved: root.setVolume(ratio)
        }

        // next-up card
        Rectangle {
            id: queue
            y: parent.height - height
            width: parent.width
            height: 3 * 40 + 40 + 2 * root.sp1
            radius: root.rad
            color: root.cCard
            Text {
                x: root.sp2; y: root.sp1
                text: "다음 곡"
                color: root.cAccent
                font.pixelSize: 28
                font.bold: true
            }
            Repeater {
                model: Player.upNext(root.trackIndex, tracks.count, 3)
                delegate: Item {
                    id: row
                    property var t: tracks.get(modelData)
                    y: 40 + root.sp1 + index * 40
                    width: queue.width; height: 40
                    Rectangle {
                        anchors.fill: parent
                        anchors.leftMargin: root.sp1; anchors.rightMargin: root.sp1
                        radius: root.rad / 2
                        color: rowTap.pressed ? root.cCardHi : "transparent"
                    }
                    Text {
                        x: root.sp2 + root.sp1; anchors.verticalCenter: parent.verticalCenter
                        text: row.t.title
                        width: queue.width * 0.5
                        color: root.cText; font.pixelSize: 28; elide: Text.ElideRight
                    }
                    Text {
                        x: queue.width * 0.5 + root.sp3; anchors.verticalCenter: parent.verticalCenter
                        text: row.t.artist
                        width: queue.width * 0.5 - 2 * root.sp4 - 80
                        color: root.cSub; font.pixelSize: 28; elide: Text.ElideRight
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: root.sp3
                        anchors.verticalCenter: parent.verticalCenter
                        text: Player.formatTime(row.t.duration)
                        color: root.cSub; font.pixelSize: 28
                    }
                    MouseArea { id: rowTap; anchors.fill: parent; onClicked: root.selectTrack(modelData) }
                }
            }
        }
    }
}
