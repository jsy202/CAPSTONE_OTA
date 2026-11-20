import QtQuick 2.15
import "."

// One cell of the dock row: width is a multiple of the dock pitch, content is centred by the caller (anchors.centerIn: parent).
Item {
    property real w: 1
    width: Math.round(Theme.dockUnit * w); height: Theme.dockH
}
