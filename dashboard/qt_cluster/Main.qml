import QtQuick 2.15
import QtQuick.Window 2.15

// The one fixed cluster screen. Every value comes from the C++ side (capstoneState), which only
// updates after a CRC-, range- and counter-checked CAN 0x200 frame.
Window {
    id: rootWindow
    objectName: "rootWindow"
    visible: true
    width: 1280
    height: 480
    color: "#02040a"
    title: "CAPSTONE OTA Cluster"
    readonly property bool capstoneGaugeReady: cluster.width > 0 && cluster.visible
    Item {
        anchors.fill: parent
        Cluster {
            id: cluster
            objectName: "capstoneCluster"
            transformOrigin: Item.TopLeft
            scale: Math.min(parent.width / 1920, parent.height / 720)
            valid: capstoneState.valid
            speed: capstoneState.valid ? capstoneState.speed : 0
            rpm: capstoneState.valid ? capstoneState.rpm : 0
            gear: capstoneState.gear > 0 ? "D" : "N"
        }
    }
}
