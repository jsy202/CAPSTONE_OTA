import QtQuick 2.15
Item {
    id: overlay
    anchors.fill: parent
    z: 1000
    property real displayedSpeed: speedoModel.currentValue
    property int displayedRpm: rpmModel.rpm
    property int displayedGear: capstoneState.gear
    property bool displayedWarning: checkEngineLightModel.on
    property bool displayedValid: capstoneState.valid
    Rectangle {
        width: 620; height: 54; x: 330; y: 420
        color: "#14212c"; radius: 8
        Text {
            anchors.centerIn: parent; color: "white"; font.pixelSize: 22
            text: overlay.displayedValid
                  ? overlay.displayedSpeed.toFixed(1) + " km/h   ·   " + overlay.displayedRpm + " RPM   ·   G" + overlay.displayedGear + (overlay.displayedWarning ? "   CHECK ENGINE" : "")
                  : "NO FRESH VEHICLE DATA"
        }
    }
    Rectangle {
        width: 100; height: 44
        anchors.right: parent.right; anchors.bottom: parent.bottom
        anchors.rightMargin: 16; anchors.bottomMargin: 12
        radius: 8
        color: capstoneVersion === "V1" ? "#7354a5" : capstoneVersion === "V2" ? "#2164c3" : "#a8500c"
        Text {
            objectName: "capstoneVersionLabel"
            anchors.centerIn: parent
            text: capstoneVersion
            textFormat: Text.PlainText
            color: "white"; font.pixelSize: 28; font.bold: true
        }
    }
}
