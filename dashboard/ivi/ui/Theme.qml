pragma Singleton
import QtQuick 2.15

// Design tokens for the head unit. Every colour, space, radius, type size and icon size in the UI comes from here.
QtObject {
    // colour roles
    readonly property color bg: "#131417"          // screen
    readonly property color surface: "#16181c"     // map ground, panels sitting on the screen
    readonly property color overlay: "#313236"     // floating cards and square buttons over the map
    readonly property color overlayLine: "#44464e"
    readonly property color accent: "#1fa6f9"      // the one signature blue
    readonly property color accentDim: "#2a5f8a"   // travelled part of the route
    readonly property color media: "#8a5cf6"       // media tile hue, used by the player panel
    readonly property color radio: "#ff66ae"       // radio tile hue (V1 radio panel): dock tile gradient top
    readonly property color radioDeep: "#df2a82"   // dock tile gradient bottom
    readonly property color ink: "#ffffff"
    readonly property color inkMuted: "#a9b0bd"
    readonly property color inkOnAccent: "#04121f"
    readonly property color badge: "#ff7a1a"       // installed-version badge: the monitoring screen's signature orange, not the map blue
    readonly property color inkOnBadge: "#1a0d00"
    readonly property color good: "#2ecc71"
    readonly property color warn: "#ffb020"
    readonly property color danger: "#ff5a5f"
    readonly property color segment: "#34363c"

    // spacing scale
    readonly property int s1: 8
    readonly property int s2: 16
    readonly property int s3: 24
    readonly property int s4: 32

    // corner radii
    readonly property int rTile: 14     // dock tiles
    readonly property int rButton: 12   // square map buttons
    readonly property int rPanel: 20    // map, cards, panels

    // type scale (pixels at the 1920x600 design size)
    readonly property int tXs: 28
    readonly property int tSm: 32
    readonly property int tMd: 36
    readonly property int tLg: 44
    readonly property int tXl: 56
    readonly property int tDisplay: 120   // radio frequency readout

    // icons: one 48 grid, one 4-unit stroke (see icons.js), two display sizes
    readonly property int iconGrid: 48
    readonly property int iconXs: 28
    readonly property int iconSm: 36
    readonly property int iconLg: 44
    readonly property int iconXl: 52    // free-standing dock glyphs
    readonly property real iconStroke: 3.6   // rendered stroke in px, the same at every size

    // font families (files shipped in ./fonts and registered by Ivi.qml); Hangul falls back to the system sans
    readonly property string fHead: "Space Grotesk"
    readonly property string fBody: "DM Sans"
    readonly property string fBodyMed: "DM Sans"

    // frame geometry (1920x600 strip: status bar, stage, dock)
    readonly property int topH: 64
    readonly property int dockH: 96
    readonly property int leftW: 480                       // vehicle column = 25% of the width
    readonly property int stageX: leftW + s2               // map / player starts after a one-step gutter
    readonly property int stageW: 1920 - stageX - s2
    readonly property int stageH: 600 - topH - dockH
    readonly property int tile: 64
    readonly property int button: 88
    readonly property int dockUnit: 88                     // dock pitch: every slot is a multiple of this
}
