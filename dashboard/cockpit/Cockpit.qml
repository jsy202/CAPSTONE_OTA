import QtQuick 2.15
import QtQuick.Window 2.15

// Single-monitor cockpit: real Qt cluster (live capture), OTA/field-monitor status, the IVI of the
// CURRENT slot (loaded from the release itself), and an event log. Designed at 2160x1440, scaled to fit.
Window {
    id: win
    visible: true; visibility: Window.FullScreen; color: "#05070b"; title: "CAPSTONE cockpit"
    property var d: ({})
    property string dataUrl: "file:///run/cockpit/data.json"
    readonly property var pi3: d.pi3 ? d.pi3 : null
    readonly property string pstate: pi3 ? pi3.state : "lost"
    readonly property color stateColor: pstate === "confirmed" ? "#ff5d5d" : pstate === "suspect" ? "#ffb347"
                                      : pstate === "observing" ? "#27d3a2" : "#8a94a6"

    property var lastShot: null
    onDChanged: if (d.shot && d.shot !== lastShot) {
        lastShot = d.shot
        stage.grabToImage(function (r) { r.saveToFile("/tmp/cockpit-shot.png") })
    }
    function readData() {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () { if (x.readyState === 4) { try { win.d = JSON.parse(x.responseText) } catch (e) { } } }
        x.open("GET", dataUrl + "?t=" + Date.now()); x.send()
    }
    Timer { interval: 400; running: true; repeat: true; triggeredOnStart: true; onTriggered: win.readData() }

    Item {
        id: stage; width: 2160; height: 1440; transformOrigin: Item.TopLeft
        scale: Math.min(win.width / 2160, win.height / 1440)

        // ---------------- cluster (real application, live capture) ----------------
        Rectangle { x: 0; y: 0; width: 1440; height: 540; color: "#000000"
            Image { id: clusterImg; anchors.fill: parent; cache: false; fillMode: Image.Stretch
                source: win.d.cluster ? "file:///run/cockpit/cluster.bmp?rev=" + win.d.cluster.rev : "" }
            Text { visible: !win.d.cluster || clusterImg.status !== Image.Ready; anchors.centerIn: parent; color: "#8a94a6"
                font.pixelSize: 34; text: "Cluster: no live data" }
            Rectangle { x: 14; y: 12; height: 34; width: capt.width + 24; radius: 17; color: "#aa0b0f17"
                Text { id: capt; anchors.centerIn: parent; color: "#9fb0c8"; font.pixelSize: 17
                    text: "CLUSTER  ·  real Qt application, live capture" } }
        }

        // ---------------- OTA / field monitor ----------------
        Rectangle { x: 1440; y: 0; width: 720; height: 540; color: "#0b0f17"; border.color: "#1c2431"
            Text { x: 28; y: 20; text: "OTA  ·  FIELD MONITOR"; color: "#7d8aa0"; font.pixelSize: 17; font.letterSpacing: 2 }
            // update state
            Text { x: 28; y: 58; color: "#e8f1ff"; font.pixelSize: 25; font.bold: true
                text: "IVI " + (win.d.ota && win.d.ota.version ? win.d.ota.version : "—") + (win.d.ota && win.d.ota.candidate && win.d.ota.candidate !== win.d.ota.version ? "   →   " + win.d.ota.candidate : "") }
            Text { x: 28; y: 96; color: "#9fb0c8"; font.pixelSize: 19
                text: "stage: " + (win.d.ota && win.d.ota.stage ? win.d.ota.stage : "—") }
            Row { x: 28; y: 134; spacing: 14
                Repeater { model: ["A", "B"]
                    Rectangle { width: 320; height: 64; radius: 14; color: "#141c28"
                        readonly property var slot: win.d.ota && win.d.ota.slot_view ? win.d.ota.slot_view[modelData] : null
                        border.width: 2; border.color: !slot ? "#232b38" : slot.role === "committed" ? "#27d3a2" : slot.role === "trial" ? "#ffb347"
                                       : slot.role === "recovery_failed" ? "#ff5d5d" : "#232b38"
                        Text { x: 16; y: 9; color: "#e8f1ff"; font.pixelSize: 20; font.bold: true; text: "Slot " + modelData + "  " + (slot && slot.version ? slot.version : "empty") }
                        Text { x: 16; y: 37; color: "#9fb0c8"; font.pixelSize: 16; text: slot ? slot.role : "" } } } }
            // Pi3
            Rectangle { x: 28; y: 222; width: 664; height: 4; color: "#1c2431" }
            Text { x: 28; y: 240; text: "Pi3 VERIFIER (independent, bus-side)"; color: "#7d8aa0"; font.pixelSize: 17; font.letterSpacing: 1 }
            Rectangle { x: 28; y: 276; height: 44; width: stateText.width + 40; radius: 22; color: Qt.rgba(0, 0, 0, 0.35); border.color: win.stateColor; border.width: 2
                Text { id: stateText; anchors.centerIn: parent; color: win.stateColor; font.pixelSize: 22; font.bold: true
                    text: win.pstate.toUpperCase() + (win.pi3 && win.pi3.state !== "observing" && win.pi3.cls && win.pi3.cls !== "normal" ? "  ·  " + win.pi3.cls : "") } }
            Text { x: 28; y: 336; color: "#9fb0c8"; font.pixelSize: 18
                text: win.pi3 ? "rules fired: " + (win.pi3.fired && win.pi3.fired.length ? win.pi3.fired.join(", ") : "none") : "no verdict from Pi3" }
            Text { x: 28; y: 366; color: "#9fb0c8"; font.pixelSize: 18
                text: win.pi3 ? "monitored " + Math.round(win.pi3.monitored_s) + " s since probation start" : "" }
            // CAN truth next to what the cluster shows, so viewers can compare with the live cluster on the left
            readonly property var can: win.d.tap ? win.d.tap.can : null
            readonly property var shown: win.d.tap ? win.d.tap.cluster : null
            readonly property bool lagging: shown !== null && shown !== undefined && shown.behind_frames >= 3
            readonly property real gap: can && shown ? can.speed - shown.speed : 0
            readonly property real limitMs: win.pi3 && win.pi3.latency_limit_ms ? win.pi3.latency_limit_ms : 100
            readonly property var delayMs: win.d.tap ? win.d.tap.delay_ms : null
            Rectangle { x: 28; y: 398; width: 212; height: 96; radius: 16; color: "#141c28"; border.color: "#27d3a2"; border.width: 2
                Text { x: 14; y: 8; color: "#7d8aa0"; font.pixelSize: 13; font.letterSpacing: 1; text: "CAN BUS  ·  NOW" }
                Text { x: 14; y: 26; color: "#27d3a2"; font.pixelSize: 46; font.bold: true
                    text: parent.parent.can ? Math.round(parent.parent.can.speed) : "—" }
                Text { x: 118; y: 50; color: "#7d8aa0"; font.pixelSize: 17; text: "km/h" }
                Text { x: 14; y: 74; color: "#9fb0c8"; font.pixelSize: 14
                    text: parent.parent.can ? "rpm " + parent.parent.can.rpm + "  ·  gear " + parent.parent.can.gear : "no CAN data" } }
            Rectangle { x: 252; y: 398; width: 212; height: 96; radius: 16; color: "#141c28"; border.width: 2
                border.color: parent.lagging ? "#ff5d5d" : "#232b38"
                Text { x: 14; y: 8; color: "#7d8aa0"; font.pixelSize: 13; font.letterSpacing: 1; text: "CLUSTER SHOWS" }
                Text { x: 14; y: 26; font.pixelSize: 46; font.bold: true; color: parent.parent.lagging ? "#ff5d5d" : "#e8f1ff"
                    text: parent.parent.shown ? Math.round(parent.parent.shown.speed) : "—" }
                Text { x: 118; y: 50; color: "#7d8aa0"; font.pixelSize: 17; text: "km/h" }
                Text { x: 14; y: 74; font.pixelSize: 14; color: parent.parent.lagging ? "#ff8a8a" : "#9fb0c8"
                    text: !parent.parent.shown ? "no data" : parent.parent.lagging ? "behind " + parent.parent.shown.behind_frames + " frames · " + (parent.parent.shown.behind_frames / 10).toFixed(1) + " s" : "in sync with CAN" } }
            Rectangle { x: 476; y: 398; width: 216; height: 96; radius: 16; color: "#141c28"; border.width: 2
                readonly property real stopped: win.pi3 ? win.pi3.bus_stopped_s : 0
                readonly property bool over: parent.delayMs !== null && parent.delayMs !== undefined && parent.delayMs > parent.limitMs
                border.color: over ? "#ff5d5d" : "#232b38"
                Text { x: 14; y: 8; color: "#7d8aa0"; font.pixelSize: 13; font.letterSpacing: 1; text: "CLUSTER DELAY" }
                Text { x: 14; y: 26; font.pixelSize: 40; font.bold: true; color: parent.over ? "#ff5d5d" : "#27d3a2"
                    text: parent.parent.delayMs === null || parent.parent.delayMs === undefined ? "—" : Math.round(parent.parent.delayMs) }
                Text { x: 124; y: 46; color: "#7d8aa0"; font.pixelSize: 17; text: "ms" }
                Text { x: 14; y: 74; font.pixelSize: 14; color: parent.over ? "#ff8a8a" : "#9fb0c8"
                    text: (parent.over ? "OVER the limit" : "limit") + " ≤ " + Math.round(parent.parent.limitMs) + " ms" } }
            Text { x: 28; y: 498; font.pixelSize: 14; color: win.pstate === "confirmed" ? "#ff8a8a" : "#7d8aa0"
                text: win.pstate === "confirmed" ? "Pi3: requirement violated  →  ERROR, rollback at the next safe window"
                      : win.pi3 && win.pi3.last_window && win.pi3.last_window.lat_p99_ms !== null ? "Pi3 decision window p99 " + win.pi3.last_window.lat_p99_ms.toFixed(1) + " ms" : "Pi3: collecting" }
            Text { x: 28; y: 518; font.pixelSize: 14
                readonly property real stopped: win.pi3 ? win.pi3.bus_stopped_s : 0
                color: stopped >= 5 ? "#27d3a2" : "#9fb0c8"
                text: !win.pi3 ? "vehicle: no Pi3 data" : stopped >= 5 ? "vehicle stopped " + Math.round(stopped) + " s  ·  safe window open"
                      : stopped > 0 ? "vehicle stopped " + Math.round(stopped) + " s" : "vehicle driving  ·  no rollback while driving" }
        }

        // ---------------- IVI of the current slot ----------------
        Rectangle { x: 0; y: 540; width: 2160; height: 675; color: "#0b0f17"; clip: true
            Loader { id: iviLoader; width: 1920; height: 600; transformOrigin: Item.TopLeft; scale: 1.125
                source: win.d.ivi_ui ? win.d.ivi_ui : ""
                onLoaded: if (item && item.hasOwnProperty("runDir")) item.runDir = "file:///run/capstone-ivi/" }
            Text { visible: iviLoader.status !== Loader.Ready; anchors.centerIn: parent; color: "#8a94a6"; font.pixelSize: 34
                text: win.d.ivi_ui ? "IVI loading…" : "IVI not installed" }
        }

        // ---------------- event log ----------------
        Rectangle { x: 0; y: 1215; width: 2160; height: 225; color: "#080b11"; border.color: "#1c2431"
            Text { x: 28; y: 12; text: "EVENT LOG"; color: "#7d8aa0"; font.pixelSize: 15; font.letterSpacing: 2 }
            Column { x: 28; y: 38; spacing: 2
                Repeater { model: win.d.log ? win.d.log.slice(-7) : []
                    Text { color: modelData.text.indexOf("Rollback") === 0 || modelData.text.indexOf("confirmed") >= 0 ? "#ff8a8a"
                                : modelData.text.indexOf("Commit") === 0 ? "#6be3bd" : "#aeb9cc"
                        font.pixelSize: 19; text: modelData.t + "   " + modelData.text } } }
        }
    }
}
