import QtQuick 2.15
import "."

// Rounded-square gradient app tile of the bottom dock.
Rectangle {
    id: tile
    property string kind: ""
    property color c1: "#888888"
    property color c2: "#444444"
    property bool active: false
    signal clicked()
    width: Theme.tile; height: Theme.tile; radius: Theme.rTile
    gradient: Gradient {
        GradientStop { position: 0.0; color: tile.c1 }
        GradientStop { position: 1.0; color: tile.c2 }
    }
    Icon { anchors.centerIn: parent; kind: tile.kind; size: Theme.iconLg; knock: tile.c2 }
    Rectangle {   // marks the page that is open
        visible: tile.active; width: 20; height: 4; radius: 2; color: Theme.ink
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.bottom; topMargin: 3 }
    }
    MouseArea { anchors.fill: parent; onClicked: tile.clicked() }
}
