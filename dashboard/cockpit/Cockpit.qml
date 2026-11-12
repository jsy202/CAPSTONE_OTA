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
    readonly property color inkMuted: "#7c8696"
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.09)
    readonly property color stateColor: pstate === "confirmed" ? critical : pstate === "suspect" ? warn
                                      : pstate === "observing" ? accent : inkMuted

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
    onOtaChanged: {
        if (win.ota) {
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

    function stageFraction(stage) {
        var map = {metadata: 0.08, signature: 0.16, signature_verified: 0.22, download: 0.32, archive_verify: 0.45,
                   install: 0.58, trial: 0.72, verify: 0.85, verification_failed: 0.85, recover: 0.72,
                   recovery_verified: 0.88, recheck: 0.94, committed: 1.0, rolled_back: 1.0,
                   recovery_failed: 0.6, failed: 0.4, error: 0.3}
        return map[stage] !== undefined ? map[stage] : 0
    }
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
                font.family: fSans.name; font.pixelSize: 30; text: "CLUSTER · NO SIGNAL" }
            Row { x: 14; y: 14; spacing: 8
                Rectangle { width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter; color: win.critical }
                Text { color: win.inkMuted; font.family: fMono.name; font.pixelSize: 14; font.letterSpacing: 1
                    text: "CLUSTER · LIVE" } }
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

            // header row: eyebrow + a stage-driven progress ring that settles into the version readout --
            // the same real-world pattern a Tesla-style update indicator uses (ring fills through the
            // install stages, then the version text takes over once it closes), not an instant text swap.
            Item { x: 32; y: 22; width: panel.width - 64; height: 56
                Text { anchors.verticalCenter: parent.verticalCenter; text: "OTA MONITOR"; color: win.inkMuted
                    font.family: fMono.name; font.pixelSize: 14; font.letterSpacing: 2 }
                Row { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 14
                    Item {
                        id: ringWrap; width: 36; height: 36
                        property real fraction: win.stageFraction(win.stage)
                        property color ringColor: (win.stage === "recovery_failed" || win.stage === "failed" || win.stage === "error") ? win.critical
                                                 : (win.stage === "verification_failed") ? win.warn : win.accent
                        property bool _wasComplete: false
                        Behavior on fraction { NumberAnimation { duration: 550; easing.type: Easing.OutCubic } }
                        Behavior on ringColor { ColorAnimation { duration: 400 } }
                        onFractionChanged: {
                            ringCanvas.requestPaint()
                            if (fraction >= 0.999 && !_wasComplete) { _wasComplete = true; completePulse.restart() }
                            else if (fraction < 0.999) { _wasComplete = false }
                        }
                        onRingColorChanged: ringCanvas.requestPaint()
                        Canvas {
                            id: ringCanvas; anchors.fill: parent
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var cx = width / 2, cy = height / 2, r = width / 2 - 3
                                ctx.lineWidth = 4; ctx.lineCap = "butt"
                                ctx.strokeStyle = "#1a2029"
                                ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
                                ctx.strokeStyle = Qt.rgba(ringWrap.ringColor.r, ringWrap.ringColor.g, ringWrap.ringColor.b, 1)
                                ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + ringWrap.fraction * Math.PI * 2); ctx.stroke()
                            }
                        }
                    }
                    Text {
                        id: verText; anchors.verticalCenter: parent.verticalCenter
                        color: ringWrap.fraction >= 0.999 ? win.ink : win.accent
                        font.family: fGroteskMed.name; font.pixelSize: 20
                        transformOrigin: Item.Right
                        text: Transitions.versionText(win.ota)
                        SequentialAnimation { id: completePulse
                            NumberAnimation { target: verText; property: "scale"; to: 1.14; duration: 140; easing.type: Easing.OutQuad }
                            NumberAnimation { target: verText; property: "scale"; to: 1.0; duration: 260; easing.type: Easing.OutBack; easing.overshoot: 2.0 } }
                    } } }
            Rectangle { x: 32; y: 90; width: panel.width - 64; height: 1; color: win.hairline }

            // the one big causal visual: live cluster processing delay vs. its limit
            Column {
                x: 32; y: 120; width: panel.width - 64; spacing: 14
                Text { text: "클러스터 처리 지연"; color: win.inkMuted; font.family: fSansMed.name; font.pixelSize: 15; font.letterSpacing: 1 }
                Row { spacing: 14
                    Text { text: panel.rawDelay === null || panel.rawDelay === undefined ? "—" : Math.round(panel.displayDelay)
                        color: panel.tone; font.family: fGroteskBold.name; font.pixelSize: 124
                        Behavior on color { ColorAnimation { duration: 450 } } }
                    Text { text: "ms"; color: win.inkMuted; font.family: fMono.name; font.pixelSize: 26
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 18 } }
                Rectangle { width: parent.width; height: 6; color: "#11151b"
                    Rectangle { width: parent.width * Math.min(1, panel.ratio * 0.62); height: parent.height
                        color: panel.tone
                        Behavior on width { NumberAnimation { duration: 650; easing.type: Easing.OutCubic } }
                        Behavior on color { ColorAnimation { duration: 450 } } }
                    Rectangle { x: parent.width * 0.62 - 1; width: 2; height: parent.height; color: win.ink; opacity: 0.5 } }
                // instrument ticks under the bar, the only decorative nod to a gauge face
                Row { width: parent.width
                    Repeater { model: 4
                        Item { width: parent.width / 4; height: 14
                            Rectangle { x: 0; width: 1; height: 8; color: win.hairline }
                            Text { x: 4; y: -2; text: (index * 50); color: win.inkMuted; font.family: fMono.name; font.pixelSize: 11 } } } }
            }

            // status: a flat left-edge stripe, not a filled rounded pill
            Row { x: 32; y: 366; width: panel.width - 64; height: 48; spacing: 16
                Rectangle { width: 4; height: parent.height; color: win.stateColor
                    Behavior on color { ColorAnimation { duration: 400 } } }
                Text { anchors.verticalCenter: parent.verticalCenter; color: win.stateColor; font.family: fGroteskMed.name; font.pixelSize: 24
                    Behavior on color { ColorAnimation { duration: 400 } }
                    text: win.pstate === "confirmed" ? "한계 초과 · 복구 결정"
                        : win.pstate === "suspect" ? "징후 포착 · 관찰 중"
                        : win.pstate === "observing" ? "정상 관측 중" : "PI3 신호 없음" } }
            Rectangle { x: 32; y: 426; width: panel.width - 64; height: 1; color: win.hairline }

            // CAN truth vs. what the cluster shows — one flat row, hairline-divided, no card boxes
            Row { x: 32; y: 450; width: panel.width - 64; height: 90
                readonly property bool lagging: panel.shown !== null && panel.shown !== undefined && panel.shown.behind_frames >= 3
                Column { width: parent.width / 2; spacing: 6
                    Text { color: win.inkMuted; font.family: fMono.name; font.pixelSize: 13; font.letterSpacing: 1; text: "CAN BUS" }
                    Row { spacing: 8
                        Text { color: win.accent; font.family: fMono.name; font.pixelSize: 40; text: panel.can ? Math.round(panel.can.speed) : "—" }
                        Text { color: win.inkMuted; font.family: fSans.name; font.pixelSize: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; text: "km/h" } } }
                Rectangle { width: 1; height: parent.height; color: win.hairline }
                Column { x: parent.width / 2 + 24; width: parent.width / 2 - 24; spacing: 6
                    Text { color: win.inkMuted; font.family: fMono.name; font.pixelSize: 13; font.letterSpacing: 1; text: "CLUSTER SHOWS" }
                    Row { spacing: 8
                        Text { color: parent.parent.parent.lagging ? win.critical : win.ink; font.family: fMono.name; font.pixelSize: 40
                            text: panel.shown ? Math.round(panel.shown.speed) : "—" }
                        Text { color: win.inkMuted; font.family: fSans.name; font.pixelSize: 16; anchors.bottom: parent.bottom; anchors.bottomMargin: 7; text: "km/h" } } } }
        }

        // ---------------- IVI of the current slot (real app, live — untouched) ----------------
        Rectangle { x: 0; y: 540; width: 2160; height: 675; color: "#0a0e15"; clip: true
            Loader { id: iviLoader; width: 1920; height: 600; transformOrigin: Item.TopLeft; scale: 1.125
                source: win.d.ivi_ui ? win.d.ivi_ui : ""
                onLoaded: if (item && item.hasOwnProperty("runDir")) item.runDir = "file:///run/capstone-ivi/" }
            Text { visible: iviLoader.status !== Loader.Ready; anchors.centerIn: parent; color: win.inkMuted; font.family: fSans.name; font.pixelSize: 32
                text: win.d.ivi_ui ? "IVI loading…" : "IVI not installed" }
        }

        // ---------------- event log (unchanged) ----------------
        Rectangle { x: 0; y: 1215; width: 2160; height: 225; color: "#070a10"
            Rectangle { width: parent.width; height: 1; color: win.hairline }
            Text { x: 28; y: 12; text: "EVENT LOG"; color: win.inkMuted; font.family: fMono.name; font.pixelSize: 14; font.letterSpacing: 2 }
            Column { x: 28; y: 38; spacing: 2
                Repeater { model: win.d.log ? win.d.log.slice(-7) : []
                    Text { color: modelData.text.indexOf("Rollback") === 0 || modelData.text.indexOf("confirmed") >= 0 ? "#ff8a8a"
                                : modelData.text.indexOf("Commit") === 0 ? win.accent : "#aeb9cc"
                        font.family: fSans.name; font.pixelSize: 18; text: modelData.t + "   " + modelData.text } } }
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
