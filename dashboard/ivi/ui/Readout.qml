import QtQuick 2.15
import "."

// Large figure with a smaller fraction and a label under it (dock climate readouts).
Column {
    property string whole: ""
    property string fraction: ""
    property string label: ""
    property color labelColor: Theme.inkMuted
    spacing: 0
    Item {
        anchors.horizontalCenter: parent.horizontalCenter
        width: wholeText.width + fractionText.width; height: wholeText.height
        Text { id: wholeText; text: parent.parent.whole; color: Theme.ink; font.pixelSize: Theme.tLg; font.family: Theme.fHead }
        Text { id: fractionText; x: wholeText.width; anchors.baseline: wholeText.baseline; text: parent.parent.fraction; color: Theme.ink
            font.pixelSize: Theme.tXs; font.family: Theme.fHead }
    }
    Text { anchors.horizontalCenter: parent.horizontalCenter; text: parent.label; color: parent.labelColor
        font.pixelSize: Theme.tXs; font.family: Theme.fBodyMed }
}
