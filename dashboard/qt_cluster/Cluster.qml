import QtQuick 2.15

// Instrument cluster, designed at 1920x720 and scaled to fit. Two ring gauges (speed / rpm) with a
// trip computer between them. Every value is a plain property so the C++ side only has to bind it.
Item {
    id: root
    width: 1920; height: 720
    property real speed: 0            // km/h
    property real maxSpeed: 220
    property real rpm: 0              // revolutions per minute
    property real maxRpm: 8000
    property string gear: "D"
    property real fuel: 0.6           // 0..1
    property real coolant: 0.45       // 0..1
    property int range: 480           // km
    property real trip: 126.5         // km
    property string timer: "2:30"
    property real average: 11.7       // km/L
    property real leadSpeed: 100      // km/h of the set cruise speed
    property real outside: 25         // deg C
    property real odometer: 1576      // km
    property bool valid: true

    readonly property real sweepStart: 150     // degrees, clockwise from east (screen coordinates)
    readonly property real sweepSpan: 240
    readonly property color ink: "#f2f4f8"
    readonly property color inkDim: "#8d9bb0"

    FontLoader { id: face; source: "fonts/DMSans-Regular.ttf" }
    readonly property string family: face.status === FontLoader.Ready ? face.name : "sans-serif"

    Rectangle { anchors.fill: parent; color: "#02040a" }
    Rectangle {   // soft glow behind the trip computer
        x: 560; y: 80; width: 800; height: 640; radius: 400
        gradient: Gradient { GradientStop { position: 0; color: "#00101b2a" } GradientStop { position: 1; color: "#0a121d" } }
        opacity: 0.55
    }

    function rad(deg) { return deg * Math.PI / 180 }

    component Ring: Item {
        id: ring
        width: 720; height: 720
        property real value: 0              // 0..1
        property color trackColor
        property color lineColor
        property color arcA
        property color arcB
        property color discA
        property color discB
        property real level: 0              // bottom scale fill 0..1
        property real shown: 0              // the value of the arc that was last painted (the digits follow this)
        property string leftTag: "E"
        property string rightTag: "F"
        readonly property real cx: 360
        readonly property real cy: 360
        function a(d) { return d * Math.PI / 180 }

        // Static layers: painted once (and when the bottom scale level changes), never per speed frame.
        Canvas {
            id: base
            anchors.fill: parent
            renderStrategy: Canvas.Cooperative
            property real lv: ring.level
            onLvChanged: requestPaint()
            onPaint: {
                var c = getContext("2d"); c.reset()
                var cx = ring.cx, cy = ring.cy, a = ring.a
                var s = root.sweepStart, w = root.sweepSpan
                c.beginPath(); c.arc(cx, cy, 245, a(s), a(s + w), false)
                c.lineWidth = 92; c.strokeStyle = "#0c1119"; c.lineCap = "butt"; c.stroke()
                c.beginPath(); c.arc(cx, cy, 287, a(s), a(s + w), false)
                c.lineWidth = 3; c.strokeStyle = ring.trackColor; c.stroke()
                var d = c.createRadialGradient(cx, cy - 140, 10, cx, cy, 190)
                d.addColorStop(0, ring.discA); d.addColorStop(1, ring.discB)
                c.beginPath(); c.arc(cx, cy, 188, 0, Math.PI * 2); c.fillStyle = d; c.fill()
                c.beginPath(); c.arc(cx, cy, 188, 0, Math.PI * 2); c.lineWidth = 1.5
                c.strokeStyle = Qt.rgba(1, 1, 1, 0.10); c.stroke()
                var from = 131, to = 49
                c.beginPath(); c.arc(cx, cy, 284, a(to), a(from), false)
                c.lineWidth = 3; c.strokeStyle = "#233044"; c.stroke()
                var end = from - (from - to) * level
                c.beginPath(); c.arc(cx, cy, 284, a(end), a(from), false)
                c.lineWidth = 5; c.strokeStyle = "#e9edf3"; c.stroke()
                for (var i = 0; i < 5; ++i) {
                    var t = a(from - (from - to) * i / 4)
                    c.beginPath()
                    c.moveTo(cx + Math.cos(t) * 262, cy + Math.sin(t) * 262)
                    c.lineTo(cx + Math.cos(t) * 276, cy + Math.sin(t) * 276)
                    c.lineWidth = 2.5; c.strokeStyle = i === 2 ? "#e9edf3" : "#7b879a"; c.stroke()
                }
            }
        }
        // Live layer: only the progress arcs and their glow. The glow is stacked translucent strokes of
        // growing width (no blur filter), which is cheap enough for the Pi's software renderer.
        Canvas {
            id: live
            anchors.fill: parent
            renderStrategy: Canvas.Threaded      // JS painting must not hold the thread that answers CAN frames
            property real v: ring.value
            property var last: ({ value: 0 })    // written by the paint thread, read after the paint: no property signal
            onVChanged: requestPaint()
            onPainted: ring.shown = last.value
            onPaint: {
                var c = getContext("2d"); c.reset()
                var cx = ring.cx, cy = ring.cy, a = ring.a
                var s = root.sweepStart, w = root.sweepSpan, value = ring.value
                last.value = value
                c.lineCap = "butt"
                var lc = ring.lineColor, ac = ring.arcB
                var fade = Math.max(0, Math.min(1, (value - 0.06) / 0.14))   // no halo on a very short arc: wide strokes there only make a fan-shaped smear
                if (value > 0.002) {
                    c.lineCap = value > 0.15 ? "round" : "butt"
                    for (var k = 7; k >= 1; --k) {      // soft halo outside the thin ring
                        c.beginPath(); c.arc(cx, cy, 287, a(s), a(s + w * value), false)
                        c.lineWidth = 5 + k * 9; c.strokeStyle = Qt.rgba(lc.r, lc.g, lc.b, 0.045 * fade); c.stroke()
                    }
                    for (var j = 6; j >= 1; --j) {      // halo around the thick inner arc
                        c.beginPath(); c.arc(cx, cy, 200, a(s + 6), a(s + 6 + (w - 12) * value), false)
                        c.lineWidth = 24 + j * 10; c.strokeStyle = Qt.rgba(ac.r, ac.g, ac.b, 0.05 * fade); c.stroke()
                    }
                    c.lineCap = "butt"
                    c.beginPath(); c.arc(cx, cy, 287, a(s), a(s + w * value), false)
                    c.lineWidth = 5; c.strokeStyle = lc; c.stroke()
                    var g = c.createLinearGradient(cx - 210, cy + 200, cx - 100, cy - 220)
                    g.addColorStop(0, ring.arcB); g.addColorStop(1, ring.arcA)
                    c.beginPath(); c.arc(cx, cy, 200, a(s + 6), a(s + 6 + (w - 12) * value), false)
                    c.lineWidth = 24; c.strokeStyle = g; c.stroke()
                    // light bleeding from the arc end onto the dark track
                    var ea = a(s + 6 + (w - 12) * value), ex = cx + Math.cos(ea) * 245, ey = cy + Math.sin(ea) * 245
                    var gl = c.createRadialGradient(ex, ey, 0, ex, ey, 90)
                    gl.addColorStop(0, Qt.rgba(lc.r, lc.g, lc.b, 0.42 * fade))
                    gl.addColorStop(1, Qt.rgba(lc.r, lc.g, lc.b, 0))
                    c.beginPath(); c.arc(ex, ey, 90, 0, Math.PI * 2); c.fillStyle = gl; c.fill()
                }
            }
        }
        Text { x: 168; y: 518; width: 40; text: ring.leftTag; color: root.ink; opacity: 0.85; font.family: root.family; font.pixelSize: 30 }
        Text { x: 472; y: 518; width: 40; text: ring.rightTag; color: root.ink; opacity: 0.85; font.family: root.family; font.pixelSize: 30 }
    }

    // ---------------- speed ----------------
    Ring {
        id: speedRing
        x: -2; y: -8
        value: Math.max(0, Math.min(1, root.speed / root.maxSpeed))
        level: root.fuel
        trackColor: "#16324d"; lineColor: "#2f8fe8"
        arcA: "#8fe3f7"; arcB: "#2a9fd0"
        discA: "#2d5f86"; discB: "#0b1119"
        leftTag: "E"; rightTag: "F"
    }
    // The digits show the speed of the arc that is on screen. The arc is painted on its own thread and is late when the
    // cores are busy; digits that jumped ahead of it would disagree with the arc.
    Text { objectName: "speedDigits"; x: 38; y: 266; width: 640; horizontalAlignment: Text.AlignHCenter; color: root.ink
        text: root.valid ? Math.round(speedRing.shown * root.maxSpeed) : "--"; font.family: root.family; font.pixelSize: 112; font.weight: Font.Light }
    Text { x: 38; y: 401; width: 640; horizontalAlignment: Text.AlignHCenter; color: root.ink; opacity: 0.85
        text: "km/h"; font.family: root.family; font.pixelSize: 28 }
    Row {   // fuel pump + remaining range
        x: 38 + 640 / 2 - width / 2; y: 548; spacing: 8
        Canvas { width: 40; height: 56; anchors.verticalCenter: parent.verticalCenter
            onPaint: { var c = getContext("2d"); c.reset(); c.strokeStyle = "#e9edf3"; c.lineWidth = 3
                c.strokeRect(8, 8, 18, 40); c.strokeRect(12, 14, 10, 12)
                c.beginPath(); c.moveTo(26, 22); c.lineTo(34, 28); c.lineTo(34, 42); c.stroke()
                c.beginPath(); c.moveTo(0, 22); c.lineTo(5, 18); c.lineTo(5, 26); c.closePath(); c.fillStyle = "#e9edf3"; c.fill() } }
        Text { text: root.range; color: root.ink; font.family: root.family; font.pixelSize: 38; anchors.verticalCenter: parent.verticalCenter }
        Text { text: "km"; color: root.ink; font.family: root.family; font.pixelSize: 26; anchors.baseline: undefined; anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 4 }
    }

    // ---------------- rpm ----------------
    Ring {
        id: rpmRing
        x: 1202; y: -8
        value: Math.max(0, Math.min(1, root.rpm / root.maxRpm))
        level: root.coolant
        trackColor: "#4a3320"; lineColor: "#e8923a"
        arcA: "#ffe3d2"; arcB: "#e08a35"
        discA: "#6a4a33"; discB: "#0e0f12"
        leftTag: "C"; rightTag: "H"
    }
    Text { x: 1242; y: 266; width: 640; horizontalAlignment: Text.AlignHCenter; color: root.ink
        text: (rpmRing.shown * root.maxRpm / 1000).toFixed(1); font.family: root.family; font.pixelSize: 112; font.weight: Font.Light }
    Text { x: 1242; y: 401; width: 640; horizontalAlignment: Text.AlignHCenter; color: root.ink; opacity: 0.85
        text: "x1000 rpm"; font.family: root.family; font.pixelSize: 28 }
    Canvas {   // coolant thermometer over waves
        x: 1242 + 320 - 20; y: 548; width: 40; height: 56
        onPaint: { var c = getContext("2d"); c.reset(); c.strokeStyle = "#e9edf3"; c.lineWidth = 3
            c.beginPath(); c.moveTo(20, 4); c.lineTo(20, 32); c.stroke()
            for (var k = 0; k < 3; ++k) { c.beginPath(); c.moveTo(24, 8 + k * 8); c.lineTo(30, 8 + k * 8); c.stroke() }
            c.beginPath(); c.arc(20, 36, 4, 0, Math.PI * 2); c.fillStyle = "#e9edf3"; c.fill()
            for (var j = 0; j < 2; ++j) { c.beginPath(); c.moveTo(2, 46 + j * 7)
                c.bezierCurveTo(8, 40 + j * 7, 12, 52 + j * 7, 20, 46 + j * 7)
                c.bezierCurveTo(28, 40 + j * 7, 32, 52 + j * 7, 38, 46 + j * 7); c.stroke() } } }

    // ---------------- centre: cruise gap + trip computer ----------------
    Item {
        x: 742; y: 112; width: 440; height: 60
        Canvas { x: 0; y: 12; width: 210; height: 36   // lead car, gap bars, own car
            onPaint: { var c = getContext("2d"); c.reset()
                function car(x0, flip) { c.save(); c.translate(x0, 0); if (flip) { c.scale(-1, 1); c.translate(-58, 0) }
                    c.beginPath(); c.moveTo(2, 28); c.lineTo(4, 20); c.quadraticCurveTo(12, 18, 18, 10)
                    c.quadraticCurveTo(26, 2, 42, 4); c.quadraticCurveTo(52, 8, 56, 18); c.lineTo(58, 28)
                    c.quadraticCurveTo(58, 31, 54, 31); c.lineTo(6, 31); c.quadraticCurveTo(2, 31, 2, 28)
                    c.closePath(); c.fillStyle = "#c9ced6"; c.fill(); c.restore() }
                car(0, false)
                for (var i = 0; i < 4; ++i) { c.fillStyle = "#1db7f2"; c.fillRect(72 + i * 11, 14 - i, 8 + i, 8 + i * 2) }
                car(136, true) } }
        Row { x: 212; y: 0; spacing: 4
            Text { text: Math.round(root.leadSpeed); color: root.ink; font.family: root.family; font.pixelSize: 44; anchors.baseline: unit.baseline }
            Text { id: unit; text: "km/h"; color: root.ink; font.family: root.family; font.pixelSize: 25 } }
    }
    Rectangle { x: 742; y: 172; width: 436; height: 1.5; color: Qt.rgba(1, 1, 1, 0.22) }
    Text { x: 742; y: 192; width: 436; horizontalAlignment: Text.AlignHCenter; color: root.ink
        text: "Current Trip"; font.family: root.family; font.pixelSize: 38 }

    component Metric: Item {
        property string label; property string value; property string unit
        height: 70; width: 440
        Text { x: 54; y: 14; text: parent.label; color: root.inkDim; font.family: root.family; font.pixelSize: 36 }
        Text { id: v; x: 178; width: 150; y: 0; horizontalAlignment: Text.AlignRight; text: parent.value; color: root.ink; font.family: root.family; font.pixelSize: 48 }
        Text { x: 340; y: 22; text: parent.unit; color: root.ink; font.family: root.family; font.pixelSize: 26 }
    }
    Metric { x: 742; y: 272; label: "Trip"; value: root.trip.toFixed(1); unit: "km" }
    Metric { x: 742; y: 348; label: "Timer"; value: root.timer; unit: "h:m" }
    Metric { x: 742; y: 426; label: "Avg."; value: root.average.toFixed(1); unit: "km/L" }

    Item {   // efficiency bar, 0..30 km/L with a marker
        x: 794; y: 506; width: 332; height: 70
        Rectangle { y: 14; width: parent.width; height: 11; color: "#1c2a3a" }
        Rectangle { y: 14; width: parent.width * Math.min(1, root.average / 30); height: 11; color: "#1db7f2" }
        Rectangle { x: parent.width * 10 / 30 - 1; y: 14; width: 2; height: 11; color: "#e8f6ff"; opacity: 0.6 }
        Rectangle { x: parent.width * 20 / 30 - 1; y: 14; width: 2; height: 11; color: "#e8f6ff"; opacity: 0.4 }
        Canvas { x: parent.width * Math.min(1, root.average / 30) - 8; y: -4; width: 16; height: 12
            property real mark: root.average
            onMarkChanged: requestPaint()
            onPaint: { var c = getContext("2d"); c.reset(); c.beginPath(); c.moveTo(1, 1); c.lineTo(15, 1); c.lineTo(8, 11); c.closePath(); c.fillStyle = "#ffffff"; c.fill() } }
        Repeater { model: [["0", 0], ["10", 10], ["20", 20], ["30", 30]]
            Text { x: parent.width * modelData[1] / 30 - width / 2 + (modelData[1] === 0 ? 5 : modelData[1] === 30 ? -6 : 0); y: 34
                text: modelData[0]; color: root.inkDim; font.family: root.family; font.pixelSize: 24 } }
    }

    // ---------------- bottom strip ----------------
    Rectangle {
        x: 626; y: 630; width: 668; height: 90; radius: 14
        color: "#101b29"
        Rectangle { y: parent.height - 14; width: parent.width; height: 14; color: parent.color }
        Text { x: 60; y: 18; text: Math.round(root.outside); color: root.ink; font.family: root.family; font.pixelSize: 40 }
        Text { x: 60 + (Math.round(root.outside) > 9 ? 52 : 28); y: 30; text: "°C"; color: root.ink; font.family: root.family; font.pixelSize: 26 }
        Text { anchors.right: parent.right; anchors.rightMargin: 44; y: 12; text: root.gear; color: root.ink; font.family: root.family; font.pixelSize: 48 }
    }
    Row { anchors.right: parent.right; anchors.rightMargin: 12; y: 658; spacing: 4
        Text { text: Math.round(root.odometer); color: root.ink; font.family: root.family; font.pixelSize: 40; anchors.baseline: odoUnit.baseline }
        Text { id: odoUnit; text: "km"; color: root.ink; font.family: root.family; font.pixelSize: 26 } }
}
