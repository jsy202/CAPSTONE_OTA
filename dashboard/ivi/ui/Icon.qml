import QtQuick 2.15
import "."
import "icons.js" as Icons

// One vector icon from icons.js, drawn crisp at any size on the shared 48 grid.
Canvas {
    id: ic
    property string kind: ""
    property color color: Theme.ink
    property color knock: Theme.bg          // cut-out colour = the surface underneath
    property int size: Theme.iconSm
    width: size; height: size
    onPaint: {
        var c = getContext("2d")
        c.reset()
        c.scale(width / Theme.iconGrid, height / Theme.iconGrid)
        Icons.draw(c, kind, color.toString(), knock.toString(), Math.min(6, Theme.iconStroke * Theme.iconGrid / width))
    }
    onKindChanged: requestPaint()
    onColorChanged: requestPaint()
    onKnockChanged: requestPaint()
    onSizeChanged: requestPaint()
}
