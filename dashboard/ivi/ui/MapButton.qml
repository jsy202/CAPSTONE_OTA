import QtQuick 2.15
import "."

// Square control floating on the map (right edge). Content is whatever the caller puts inside.
Rectangle {
    id: btn
    default property alias content: slot.data
    signal clicked()
    width: Theme.button; height: Theme.button; radius: Theme.rButton; color: Theme.overlay
    Item { id: slot; anchors.fill: parent }
    MouseArea { anchors.fill: parent; onClicked: btn.clicked() }
}
