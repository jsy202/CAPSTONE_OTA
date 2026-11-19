import QtQuick 2.15
import "."
import "ivi.js" as H

// FM radio panel shown by the V1 build in the stage area. VISUAL ONLY: there is no tuner hardware, but the controls
// behave like a radio (previous/next/preset step through the five stations, the needle follows, on-air toggles mute).
// Station table and dial maths are in ivi.js. Static parts paint once; only the needle animates, and only while tuning.
Rectangle {
    id: panel
    radius: Theme.rPanel
    color: Theme.surface

    property int station: 0
    property bool onAir: true
    readonly property var current: H.RADIO_STATIONS[station]
    readonly property int pad: Theme.s4
    readonly property int dialX: 560
    readonly property int dialW: width - pad - dialX

    function tune(index) { station = index }

    // ---- header: station name, band, signal strength, on-air toggle ----
    Item {
        id: header
        x: panel.pad; y: Theme.s4; width: parent.width - 2 * panel.pad; height: 56
        Rectangle {
            id: band; height: 44; width: bandText.width + 32; radius: 22; anchors.verticalCenter: parent.verticalCenter
            color: Theme.radio; opacity: panel.onAir ? 1 : 0.4
            Text { id: bandText; anchors.centerIn: parent; text: "FM"; color: Theme.inkOnBadge; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
        }
        Text {
            x: band.width + Theme.s2; anchors.verticalCenter: parent.verticalCenter
            text: panel.current.name; color: Theme.ink; font.pixelSize: Theme.tLg; font.family: Theme.fHead
        }
        Rectangle {   // on-air / mute toggle
            id: air; anchors { right: parent.right; verticalCenter: parent.verticalCenter }
            height: 56; width: 200; radius: 28
            color: panel.onAir ? Theme.radio : Theme.overlay
            Row {
                anchors.centerIn: parent; spacing: Theme.s1
                Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 16; height: 16; radius: 8
                    color: panel.onAir ? Theme.inkOnBadge : Theme.danger }
                Text { anchors.verticalCenter: parent.verticalCenter; text: panel.onAir ? "방송 중" : "음소거"
                    color: panel.onAir ? Theme.inkOnBadge : Theme.ink; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
            }
            MouseArea { anchors.fill: parent; onClicked: panel.onAir = !panel.onAir }
        }
        Row {   // signal strength: four bars, as many lit as the station's signal
            anchors { right: air.left; rightMargin: Theme.s3; verticalCenter: parent.verticalCenter }
            spacing: 6; height: 44
            Repeater {
                model: 4
                Rectangle {
                    anchors.bottom: parent.bottom; width: 10; height: 12 + index * 10; radius: 3
                    color: panel.onAir && index < panel.current.signal ? Theme.radio : Theme.overlayLine
                }
            }
        }
    }

    // ---- frequency readout ----
    Item {
        x: panel.pad; y: 104; width: panel.dialX - panel.pad - Theme.s3; height: 160; opacity: panel.onAir ? 1 : 0.4
        Text { id: freq; y: -8; text: H.radioFreqText(panel.current.freq); color: Theme.ink; font.pixelSize: Theme.tDisplay; font.family: Theme.fHead }
        Text { x: freq.width + Theme.s1; y: 80; text: "MHz"; color: Theme.inkMuted; font.pixelSize: Theme.tSm; font.family: Theme.fBodyMed }
    }

    // ---- tuner dial: scale, station marks and the needle ----
    Rectangle {
        id: dial
        x: panel.dialX; y: 104; width: panel.dialW; height: 160; radius: Theme.rPanel; color: Theme.bg
        readonly property int inset: Theme.s3
        readonly property real span: width - 2 * inset
        function xOf(f) { return inset + H.dialFraction(f) * span }
        Canvas {   // scale: a tick every 0.5 MHz, taller every 5 MHz (painted once)
            anchors.fill: parent
            renderTarget: Canvas.Image
            onPaint: {
                var c = getContext("2d"); c.reset()
                c.strokeStyle = Theme.inkMuted.toString(); c.lineWidth = 2
                for (var f = 88; f <= 108; f += 0.5) {
                    var major = f % 5 === 0, x = Math.round(dial.xOf(f)) + 1
                    c.beginPath(); c.moveTo(x, 84); c.lineTo(x, 84 + (major ? 44 : (f % 1 === 0 ? 28 : 16))); c.stroke()
                }
            }
        }
        Repeater {   // numbers under the major ticks
            model: [90, 95, 100, 105]
            Text {
                x: dial.xOf(modelData) - width / 2; y: 124 + 6
                text: modelData; color: Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBody
            }
        }
        Repeater {   // the five stations as marks above the scale
            model: H.RADIO_STATIONS
            Rectangle {
                width: 14; height: 14; radius: 7; x: dial.xOf(modelData.freq) - 7; y: 56
                visible: index !== panel.station      // the needle marks the tuned one
                color: Theme.overlayLine
            }
        }
        Rectangle {   // needle
            id: needle
            readonly property real cx: dial.xOf(panel.current.freq)
            x: cx - 2; y: 22; width: 4; height: 106; radius: 2; color: Theme.radio
            opacity: panel.onAir ? 1 : 0.4
            Behavior on x { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
            Rectangle { x: -8; y: -4; width: 20; height: 20; radius: 10; color: Theme.radio }
        }
    }

    // ---- controls: previous, five presets, next ----
    Rectangle {
        id: prevBtn; x: panel.pad; y: parent.height - panel.pad - height; width: 96; height: 96; radius: 48; color: Theme.overlay
        Icon { anchors.centerIn: parent; kind: "prev"; size: Theme.iconLg; knock: Theme.overlay }
        MouseArea { anchors.fill: parent; onClicked: panel.tune(H.stepStation(panel.station, -1)) }
    }
    Rectangle {
        id: nextBtn; x: parent.width - panel.pad - width; y: prevBtn.y; width: 96; height: 96; radius: 48; color: Theme.overlay
        Icon { anchors.centerIn: parent; kind: "next"; size: Theme.iconLg; knock: Theme.overlay }
        MouseArea { anchors.fill: parent; onClicked: panel.tune(H.stepStation(panel.station, 1)) }
    }
    Row {
        x: prevBtn.x + prevBtn.width + Theme.s3; y: prevBtn.y; height: 96
        spacing: (nextBtn.x - Theme.s3 - x - 5 * 200) / 4
        Repeater {
            model: H.RADIO_STATIONS
            Rectangle {
                readonly property bool picked: index === panel.station
                width: 200; height: 96; radius: Theme.rPanel
                color: picked ? Theme.radio : Theme.overlay
                Column {
                    anchors.centerIn: parent; spacing: 0
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.name
                        color: picked ? Theme.inkOnBadge : Theme.ink; font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: H.radioFreqText(modelData.freq)
                        color: picked ? Theme.inkOnBadge : Theme.inkMuted; font.pixelSize: Theme.tXs; font.family: Theme.fBody }
                }
                MouseArea { anchors.fill: parent; onClicked: panel.tune(index) }
            }
        }
    }
}
