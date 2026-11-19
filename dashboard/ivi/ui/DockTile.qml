import QtQuick 2.15

// Rounded-square dock tile (colours sampled from the reference photo).
// kind: "media" | "phone" | "navigation" | "fan" | "manual" | "radio" | "apps"
// "apps" has no tile background, only the 2x2 dots.
Item {
    id: root

    property string kind: "fan"
    property real size: 84
    property bool showGlyph: true

    readonly property var palette: ({
        "fan":        ["#8a8a8a", "#5e5e5e", "#515151"],
        "media":      ["#984fff", "#7434ee", "#6322e4"],
        "phone":      ["#5ad8b7", "#46b99f", "#38a49b"],
        "navigation": ["#4cb1ff", "#2791f8", "#1880f5"],
        "manual":     ["#b4b5b6", "#8a9095", "#78828a"],
        "radio":      ["#fa6ccb", "#e13f91", "#d42b73"]
    })
    readonly property bool hasTile: palette[kind] !== undefined

    width: size
    height: size

    Rectangle {
        anchors.fill: parent
        visible: root.hasTile
        radius: root.size * 0.27
        gradient: Gradient {
            GradientStop { position: 0.0; color: root.hasTile ? root.palette[root.kind][0] : "transparent" }
            GradientStop { position: 0.45; color: root.hasTile ? root.palette[root.kind][1] : "transparent" }
            GradientStop { position: 1.0; color: root.hasTile ? root.palette[root.kind][2] : "transparent" }
        }
        // faint inner rim like the photo's glassy edge
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: Math.max(1, root.size / 84)
            border.color: Qt.rgba(1, 1, 1, 0.12)
        }
    }

    Loader {
        anchors.centerIn: parent
        active: root.showGlyph
        sourceComponent: Icon {
            name: root.kind === "navigation" ? "navigation" : root.kind
            size: root.kind === "apps" ? root.size * 0.66 : root.size * 0.58
            color: "white"
        }
    }
}
