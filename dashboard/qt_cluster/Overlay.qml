import QtQuick 2.15
Rectangle {
    width: 620; height: 54; x: 330; y: 420; z: 1000
    color: "#14212c"; radius: 8
    property real displayedSpeed: speedoModel.currentValue
    property int displayedRpm: rpmModel.rpm
    property int displayedGear: capstoneState.gear
    property bool displayedWarning: checkEngineLightModel.on
    property bool displayedValid: capstoneState.valid
    Text {
        anchors.centerIn: parent; color: "white"; font.pixelSize: 22
        text: parent.displayedValid
              ? parent.displayedSpeed.toFixed(1) + " km/h   ·   " + parent.displayedRpm + " RPM   ·   G" + parent.displayedGear + (parent.displayedWarning ? "   CHECK ENGINE" : "")
              : "NO FRESH VEHICLE DATA"
    }
}
