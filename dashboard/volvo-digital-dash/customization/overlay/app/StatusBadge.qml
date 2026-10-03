import QtQuick 2.15

// Display-only OTA status badge. All text comes from the AppStatus model
// (capstoneStatus); nothing here names a state value or a version.
Item {
    id: root
    property var status: null

    readonly property string displayState: status ? status.state : "unknown"
    readonly property bool emphasized: displayState === "trial" || displayState === "restored"
    readonly property color accent: displayState === "trial" ? "#22D3EE"
                                  : displayState === "restored" ? "#F5B841"
                                  : displayState === "stable" ? "#3FB950"
                                  : "#8B949E"

    // Never take key or mouse input away from the dashboard.
    enabled: false

    Rectangle {
        id: strip
        objectName: "statusStrip"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 4
        color: root.accent
        visible: root.emphasized
    }

    Rectangle {
        id: plate
        objectName: "statusPlate"
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        height: 64
        width: Math.max(titleText.implicitWidth, detailText.implicitWidth) + 32
        radius: 8
        color: "#CC111418"
        border.width: 2
        border.color: root.emphasized ? root.accent : "#30363D"

        Column {
            anchors.centerIn: parent
            spacing: 2

            Text {
                id: titleText
                objectName: "statusTitle"
                anchors.right: parent.right
                text: root.status ? root.status.title : "SW STATUS"
                textFormat: Text.PlainText
                color: root.accent
                font.pixelSize: 22
                font.bold: true
            }

            Text {
                id: detailText
                objectName: "statusDetail"
                anchors.right: parent.right
                text: root.status ? root.status.detail : "—"
                textFormat: Text.PlainText
                color: "#E6EDF3"
                font.pixelSize: 18
            }
        }
    }
}
