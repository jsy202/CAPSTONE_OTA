import QtQuick 2.15

// Display-only OTA status badge. All text comes from the AppStatus model
// (capstoneStatus); nothing here names a state value or a version.
//
// Placement: the upstream WarningLightBar spans the bottom edge and leaves a
// free margin right of its last lamp (x >= ~1150 at 1280x480 in all eleven
// layouts), so the plate is 124 px wide and sits 6 px from the corner. Trial
// and restored states also frame the screen with thin accent lines so the
// state is recognisable from a distance without covering any gauge.
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

    // Handel Gothic (the dashboard font) draws "1" like "I". The version line
    // uses Arial Black, already bundled by upstream; if it cannot load, the
    // default font is used.
    FontLoader {
        id: versionFont
        objectName: "statusVersionFont"
        source: "qrc:/fonts/ariblk.ttf"
    }

    Rectangle {
        id: stripTop
        objectName: "statusStripTop"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 4
        color: root.accent
        visible: root.emphasized
    }

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
        anchors.margins: 6
        width: 124
        height: 50
        radius: 6
        color: "#CC111418"
        border.width: 2
        border.color: root.emphasized ? root.accent : "#30363D"

        Column {
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 8
            anchors.topMargin: 5
            spacing: 1

            Text {
                id: titleText
                objectName: "statusTitle"
                width: parent.width
                horizontalAlignment: Text.AlignRight
                text: root.status ? root.status.title : "SW STATUS"
                textFormat: Text.PlainText
                // The dashboard's global font is wide: shrink to fit, never cut.
                fontSizeMode: Text.HorizontalFit
                minimumPixelSize: 11
                color: root.accent
                font.pixelSize: 16
                font.bold: true
            }

            Text {
                id: detailText
                objectName: "statusDetail"
                width: parent.width
                horizontalAlignment: Text.AlignRight
                text: root.status ? root.status.detail : "—"
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: "#E6EDF3"
                font.family: versionFont.status === FontLoader.Ready ? versionFont.name : Qt.application.font.family
                font.pixelSize: 15
            }
        }
    }
}
