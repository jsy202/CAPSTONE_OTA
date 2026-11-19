import QtQuick 2.15
import QtQuick.Window 2.15
import "Transitions.js" as Transitions

// Single-monitor cockpit: real Qt cluster (live capture), OTA/field monitor, the IVI of the
// CURRENT slot (loaded from the release itself), and an event log. Designed at 2160x1440, scaled to fit.
//
// Visual language: flat instrument panel, not a SaaS dashboard — hairline dividers instead of
// card borders, near-zero corner radius, monospace technical tags. One signature accent (an
// orange pulled from the real cluster gauge needle, so it reads as designed with the cluster
// rather than bolted on) covers all normal/branded chrome; amber is warning, red is critical,
// and nothing else gets a decorative hue. The alert band that docks in on rollback/attack is a
// flat-color panel that extends like a physical shutter, not a centered hero card. Everything
// stays driven by the real fields already written by bench/cockpit_feeder.py.
Window {
    id: win
    visible: true; visibility: Window.FullScreen; color: "#05070a"; title: "CAPSTONE cockpit"
    property var d: ({})
    property string dataUrl: "file:///run/cockpit/data.json"
    readonly property var pi3: d.pi3 ? d.pi3 : null
    readonly property string pstate: pi3 ? pi3.state : "lost"
    readonly property var ota: d.ota ? d.ota : null
    readonly property string stage: ota && ota.stage ? ota.stage : ""

    readonly property color accent: "#ff7a1a"      // signature hue, matches the real cluster needle
    readonly property color warn: "#ffcc33"
    readonly property color critical: "#ff3b30"
    readonly property color success: "#22c55e"     // a successful commit only -- never used for anything ambient
    readonly property color ink: "#eef1f6"
    readonly property color inkMuted: "#9aa5b6"
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.09)
    readonly property color stateColor: pstate === "confirmed" ? critical : pstate === "suspect" ? warn
                                      : pstate === "observing" ? accent : warn

    FontLoader { id: fGroteskBold; source: "fonts/SpaceGrotesk-Bold.ttf" }
    FontLoader { id: fGroteskMed; source: "fonts/SpaceGrotesk-Medium.ttf" }
    FontLoader { id: fSans; source: "fonts/DMSans-Regular.ttf" }
    FontLoader { id: fSansMed; source: "fonts/DMSans-Medium.ttf" }
    FontLoader { id: fMono; source: "fonts/DMMono-Medium.ttf" }

    property var lastShot: null
    property bool _haveSnapshot: false
    property var _transitionState: ({run: null, seen: 0, kinds: {}})
    onDChanged: {
        if (d.shot && d.shot !== lastShot) {
            lastShot = d.shot
            stage_.grabToImage(function (r) { r.saveToFile("/tmp/cockpit-shot.png") })
        }
    }
    // Stage queue for the gauge: every stage the device passed through is shown for stageTimer.interval.
    property var stageQueue: []
    property var shownOta: null
    property int stagesSeen: 0
    property string stageRun: ""
    Timer { id: stageTimer; interval: 900; repeat: true
        onTriggered: {
            if (win.stageQueue.length) win.shownOta = win.stageQueue.shift()
            else { win.shownOta = null; stop() }
        } }
    function queueStages(ota) {
        var rows = ota.timeline || []
        var run = ota.timeline_id ? String(ota.timeline_id) : ""
        if (run !== stageRun) { stageRun = run; stagesSeen = _haveSnapshot ? 0 : rows.length }
        for (; stagesSeen < rows.length; stagesSeen++) {
            var row = rows[stagesSeen]
            // A refused recovery shows at once so the gauge never lags the alert band.
            if (Transitions.phase(row).kind === "failed") { stageQueue = []; shownOta = row; stageTimer.restart() }
            else stageQueue.push(row)
        }
        if (stageQueue.length && !stageTimer.running) { shownOta = stageQueue.shift(); stageTimer.restart() }
    }
    onOtaChanged: {
        if (win.ota) {
            win.queueStages(win.ota)
            var events = Transitions.consume(win._transitionState, win.ota)
            // Opening the monitor must not replay a previous completed update.
            if (win._haveSnapshot)
                for (var i = 0; i < events.length; i++) band.enqueue(events[i])
            win._haveSnapshot = true
        }
    }
    function readData() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () { if (x.readyState === 4) { try { win.d = JSON.parse(x.responseText) } catch (e) { } } }
        x.open("GET", dataUrl + "?t=" + Date.now()); x.send()
    }
    Timer { interval: 400; running: true; repeat: true; triggeredOnStart: true; onTriggered: win.readData() }

    function lerp(a, b, t) { return a + (b - a) * Math.max(0, Math.min(1, t)) }
    function rampColor(ratio) {
        if (ratio <= 0.65) return win.accent
        if (ratio <= 1.0) {
            var t1 = (ratio - 0.65) / 0.35
            return Qt.rgba(lerp(1.0, 1.0, t1), lerp(0.48, 0.80, t1), lerp(0.10, 0.20, t1), 1)
        }
        var t2 = Math.min(1, (ratio - 1.0) / 0.6)
        return Qt.rgba(1.0, lerp(0.80, 0.23, t2), lerp(0.20, 0.19, t2), 1)
    }

    Item {
        id: stage_; width: 2160; height: 1440; transformOrigin: Item.TopLeft
        scale: Math.min(win.width / 2160, win.height / 1440)

        // ---------------- cluster (real application, live capture — untouched) ----------------
        Rectangle { x: 0; y: 0; width: 1440; height: 540; color: "#000000"
            Image { id: clusterImg; anchors.fill: parent; cache: false; fillMode: Image.Stretch
                source: win.d.cluster ? "file:///run/cockpit/cluster.bmp?rev=" + win.d.cluster.rev : "" }
            Text { visible: !win.d.cluster || clusterImg.status !== Image.Ready; anchors.centerIn: parent; color: win.inkMuted
                font.family: fSans.name; font.pixelSize: 30; text: "계기판 신호 없음" }
        }

        // ---------------- OTA / field monitor — flat instrument-panel grid ----------------
        Rectangle { id: panel; x: 1440; y: 0; width: 720; height: 540; color: "#070a0f"
            Rectangle { width: 1; height: parent.height; color: win.hairline }   // left hairline seam vs. cluster
            readonly property var can: win.d.tap ? win.d.tap.can : null
            readonly property var shown: win.d.tap ? win.d.tap.cluster : null
            readonly property real limitMs: win.pi3 && win.pi3.latency_limit_ms ? win.pi3.latency_limit_ms : 100
            readonly property var rawDelay: win.d.tap ? win.d.tap.delay_ms : null
            readonly property bool over: panel.rawDelay !== null && panel.rawDelay !== undefined && panel.rawDelay > panel.limitMs
            property real displayDelay: panel.rawDelay === null || panel.rawDelay === undefined ? 0 : panel.rawDelay
            Behavior on displayDelay { NumberAnimation { duration: 650; easing.type: Easing.OutCubic } }
            readonly property real ratio: panel.limitMs > 0 ? panel.displayDelay / panel.limitMs : 0
            readonly property color tone: win.rampColor(panel.ratio)

            // ---- big progress gauge: the step running now, and how far an update or rollback has come ----
            // Each telemetry stage is shown for at least a moment (stageTimer), because a real update crosses
            // download/verify/install in well under a second and would otherwise never be seen.
            Item {
                id: gauge
                x: 32; y: 16; width: panel.width - 64; height: 160
                readonly property var live: {
                    var o = win.shownOta ? win.shownOta : win.ota
                    if (o && o.stage === "download" && win.ota && win.ota.stage === "download") o = win.ota
                    return Transitions.phase(o)
                }
                readonly property color tone: live.kind === "rollback" || live.kind === "failed" ? win.critical
                                            : live.kind === "done" ? win.success : win.accent
                property real shown: live.fraction
                Behavior on shown { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                Text { id: gaugePercent; anchors.right: parent.right; y: 0; text: gauge.live.percent; color: win.ink
                    font.family: fGroteskBold.name; font.pixelSize: 44 }
                Text { x: 0; y: 0; width: parent.width - gaugePercent.width - 12; elide: Text.ElideRight
                    text: gauge.live.label; color: gauge.tone; font.family: fGroteskBold.name; font.pixelSize: 44
                    Behavior on color { ColorAnimation { duration: 300 } } }
                Rectangle { y: 66; width: parent.width; height: 34; color: "#11151b"
                    Rectangle { width: parent.width * gauge.shown; height: parent.height; color: gauge.tone
                        Behavior on color { ColorAnimation { duration: 300 } } } }
                // version readout: it "pops" when a step completes
                property bool wasComplete: false
                onShownChanged: {
                    if (shown >= 0.999 && !wasComplete) { wasComplete = true; completePulse.restart() }
                    else if (shown < 0.999) wasComplete = false
                }
                Row { y: 112; spacing: 0
                    Text { id: verText; anchors.verticalCenter: parent.verticalCenter; color: win.ink
                        font.family: fGroteskMed.name; font.pixelSize: 48; transformOrigin: Item.Left
                        text: Transitions.versionText(win.ota)
                        SequentialAnimation { id: completePulse
                            // wait for the alert band (y0..190, ~2.3 s) to leave before popping the version
                            PauseAnimation { duration: 2400 }
                            NumberAnimation { target: verText; property: "scale"; to: 1.14; duration: 140; easing.type: Easing.OutQuad }
                            NumberAnimation { target: verText; property: "scale"; to: 1.0; duration: 260; easing.type: Easing.OutBack; easing.overshoot: 2.0 } } } }
            }
            Rectangle { x: 32; y: 190; width: panel.width - 64; height: 1; color: win.hairline }

            // the one big causal visual: live cluster processing delay vs. its limit
            // fixed line heights keep every box inside its row (measured natural heights: 28px=42, 58px=75, 92px=118)
            Text { x: 32; y: 200; text: "계기판 처리 지연"; color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28
                lineHeightMode: Text.FixedHeight; lineHeight: 34 }
            Row { x: 32; y: 232; spacing: 14; height: 110
                Text { text: panel.rawDelay === null || panel.rawDelay === undefined ? "--" : Math.round(panel.displayDelay)
                    color: panel.tone; font.family: fGroteskBold.name; font.pixelSize: 92
                    lineHeightMode: Text.FixedHeight; lineHeight: 108
                    Behavior on color { ColorAnimation { duration: 450 } } }
                Text { text: "ms"; color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 34
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 8 } }
            Rectangle { x: 32; y: 342; width: panel.width - 64; height: 12; color: "#11151b"
                Rectangle { width: parent.width * Math.min(1, panel.ratio * 0.62); height: parent.height
                    color: panel.tone
                    Behavior on width { NumberAnimation { duration: 650; easing.type: Easing.OutCubic } }
                    Behavior on color { ColorAnimation { duration: 450 } } }
                Rectangle { x: parent.width * 0.62 - 1; width: 3; height: parent.height; color: win.ink; opacity: 0.6 } }
            // limit label beside the marker, right-aligned so it never touches the delay number
            Text { x: panel.width - 32 - width; y: 342 - 34 - 2; lineHeightMode: Text.FixedHeight; lineHeight: 34; text: "한계 " + Math.round(panel.limitMs) + " ms"
                color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28 }

            // status: a flat left-edge stripe, not a filled rounded pill
            Row { x: 32; y: 366; width: panel.width - 64; height: 48; spacing: 16
                Rectangle { width: 5; height: parent.height; color: win.stateColor
                    Behavior on color { ColorAnimation { duration: 400 } } }
                Text { anchors.verticalCenter: parent.verticalCenter; color: win.stateColor; font.family: fGroteskMed.name; font.pixelSize: 34
                    Behavior on color { ColorAnimation { duration: 400 } }
                    text: win.pstate === "confirmed" ? "한계 초과 · 롤백 판단"
                        : win.pstate === "suspect" ? "징후 포착 · 관찰 중"
                        : win.pstate === "observing" ? "정상 관측 중" : "모니터 신호 없음" } }

            // CAN truth vs. what the cluster shows
            Row { x: 32; y: 432; width: panel.width - 64; height: 106
                readonly property bool lagging: panel.shown !== null && panel.shown !== undefined && panel.shown.behind_frames >= 3
                Column { width: parent.width / 2; spacing: 0
                    Text { color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28; text: "차량 속도 (CAN)"; lineHeightMode: Text.FixedHeight; lineHeight: 34 }
                    Row { spacing: 8
                        Text { color: win.accent; font.family: fGroteskBold.name; font.pixelSize: 58; lineHeightMode: Text.FixedHeight; lineHeight: 70; text: panel.can ? Math.round(panel.can.speed) : "--" }
                        Text { color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28; anchors.bottom: parent.bottom; anchors.bottomMargin: 10; text: "km/h" } } }
                Column { x: parent.width / 2 + 24; width: parent.width / 2 - 24; spacing: 0
                    Text { color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28; text: "계기판 표시"; lineHeightMode: Text.FixedHeight; lineHeight: 34 }
                    Row { spacing: 8
                        Text { color: parent.parent.parent.lagging ? win.critical : win.ink; font.family: fGroteskBold.name; font.pixelSize: 58; lineHeightMode: Text.FixedHeight; lineHeight: 70
                            text: panel.shown ? Math.round(panel.shown.speed) : "--" }
                        Text { color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 28; anchors.bottom: parent.bottom; anchors.bottomMargin: 10; text: "km/h" } } } }
        }

        // ---------------- IVI of the current slot (real app, live — untouched) ----------------
        Rectangle { x: 0; y: 540; width: 2160; height: 675; color: "#0a0e15"; clip: true
            Loader { id: iviLoader; width: 1920; height: 600; transformOrigin: Item.TopLeft; scale: 1.125
                source: win.d.ivi_ui ? win.d.ivi_ui : ""
                onLoaded: if (item && item.hasOwnProperty("runDir")) item.runDir = "file:///run/capstone-ivi/" }
            Text { visible: iviLoader.status !== Loader.Ready; anchors.centerIn: parent; color: win.inkMuted; font.family: fSans.name; font.pixelSize: 32
                text: win.d.ivi_ui ? "IVI 불러오는 중" : "IVI 설치 안 됨" }
        }

        // ---------------- event log: left = vehicle/monitor lines, right = port lines (never pushed out) ----------------
        Rectangle { x: 0; y: 1215; width: 2160; height: 225; color: "#070a10"
            id: logArea
            readonly property var rows: win.d.log ? win.d.log : []
            function isPort(text) { return text.indexOf("진단 포트") === 0 || text.indexOf("정비 포트") === 0 }
            function lineColor(text) {
                if (isPort(text)) return text.indexOf("닫힘") >= 0 || text.indexOf("거부") >= 0 ? win.success : win.warn
                if (text.indexOf("롤백") === 0 || text.indexOf("한계 초과") >= 0) return "#ff8a8a"
                if (text.indexOf("확정") === 0) return win.accent
                return "#aeb9cc"
            }
            Rectangle { width: parent.width; height: 1; color: win.hairline }
            Column { x: 28; y: 12; spacing: 0; width: 1040
                Repeater { model: logArea.rows.filter(function (r) { return !logArea.isPort(r.text) }).slice(-4)
                    Text { width: parent.width; elide: Text.ElideRight; color: logArea.lineColor(modelData.text)
                        font.family: fSansMed.name; font.pixelSize: 28; text: modelData.t + "   " + modelData.text } } }
            Column { x: 1100; y: 12; spacing: 0; width: 1032
                Repeater { model: logArea.rows.filter(function (r) { return logArea.isPort(r.text) }).slice(-4)
                    Text { width: parent.width; elide: Text.ElideRight; color: logArea.lineColor(modelData.text)
                        font.family: fSansMed.name; font.pixelSize: 28; text: modelData.t + "   " + modelData.text } } }
        }

        // ---------------- alert band: a flat panel that extends like a physical shutter ----------------
        // Docks from the TOP for a rollback or a successful commit, from the BOTTOM only for a blocked
        // attack -- the bottom edge is reserved exclusively for "something adversarial happened" so it
        // always carries that one meaning; rollback and success share the top edge since they never
        // fire at the same moment, and are told apart by tone/tag/copy, not a second docking direction.
        Item {
            id: band
            objectName: "otaEventBand"
            anchors.fill: parent
            z: 100
            property string kind: "rollback"
            property string headline: ""
            property string tag: ""
            property color tone: win.critical
            property var pending: []
            function enqueue(event) {
                pending.push(event)
                if (!seq.running) next()
            }
            function next() {
                if (!pending.length) return
                var event = pending.shift()
                kind = event.kind
                if (kind === "rollback") { tone = win.critical; headline = "롤백 준비 · 복구 이미지 검사"; tag = "[ SYS ]" }
                else if (kind === "rejected") { tone = win.critical; headline = "복구 이미지 거부 · 실행 차단"; tag = "[ SEC ]" }
                else if (kind === "recovery_error") { tone = win.critical; headline = "복구 실패 · 수동 점검 필요"; tag = "[ SYS ]" }
                else if (kind === "preparing") { tone = win.accent; headline = "업데이트 준비 · " + event.transition; tag = "[ OTA ]" }
                else if (kind === "installed") { tone = win.success; headline = "설치 완료 · " + event.transition + " · 시험 운전"; tag = "[ OTA ]" }
                else if (kind === "restored") { tone = win.success; headline = "이전 버전 복귀 완료"; tag = "[ SYS ]" }
                else { tone = win.success; headline = "업데이트 최종 승인"; tag = "[ OTA ]" }
                seq.start()
            }

            Rectangle {
                id: topBand
                anchors { left: parent.left; right: parent.right; top: parent.top }
                height: 0; color: band.tone; clip: true
                Row { x: 48; anchors.verticalCenter: parent.verticalCenter; spacing: 28
                    Rectangle { width: 108; height: 44; color: "transparent"; border.width: 2; border.color: "#05070a"
                        Text { anchors.centerIn: parent; text: band.tag; color: "#05070a"; font.family: fMono.name; font.pixelSize: 17; font.letterSpacing: 1 } }
                    Text { text: band.headline; color: "#05070a"; font.family: fGroteskBold.name; font.pixelSize: 56; font.letterSpacing: 1
                        anchors.verticalCenter: parent.verticalCenter }
                }
                Rectangle { id: topScan; y: parent.height - 4; width: 220; height: 4; color: "#05070a"; opacity: 0.55 }
            }
            Rectangle {
                id: bottomBand
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                height: 0; color: band.tone; clip: true
                Row { x: 48; anchors.verticalCenter: parent.verticalCenter; spacing: 28
                    Rectangle { width: 108; height: 44; color: "transparent"; border.width: 2; border.color: "#05070a"
                        Text { anchors.centerIn: parent; text: band.tag; color: "#05070a"; font.family: fMono.name; font.pixelSize: 17; font.letterSpacing: 1 } }
                    Text { text: band.headline; color: "#05070a"; font.family: fGroteskBold.name; font.pixelSize: 56; font.letterSpacing: 1
                        anchors.verticalCenter: parent.verticalCenter }
                }
                Rectangle { id: bottomScan; y: 0; width: 220; height: 4; color: "#05070a"; opacity: 0.55 }
            }

            SequentialAnimation {
                id: seq
                onFinished: band.next()
                PropertyAction { target: topBand; property: "height"; value: 0 }
                PropertyAction { target: bottomBand; property: "height"; value: 0 }
                PropertyAction { target: (band.kind === "rejected" ? bottomScan : topScan); property: "x"; value: -220 }
                NumberAnimation { target: band.kind === "rejected" ? bottomBand : topBand; property: "height"; to: 190
                    duration: 360; easing.type: Easing.OutCubic }
                ParallelAnimation {
                    NumberAnimation { target: band.kind === "rejected" ? bottomScan : topScan; property: "x"; from: -220; to: 2160
                        duration: 1100; easing.type: Easing.InOutQuad }
                    PauseAnimation { duration: 1100 }
                }
                PauseAnimation { duration: 500 }
                NumberAnimation { target: band.kind === "rejected" ? bottomBand : topBand; property: "height"; to: 0
                    duration: 300; easing.type: Easing.InCubic }
            }
        }
    }
}
