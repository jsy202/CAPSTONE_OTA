// Component verification (SWE.5): AppStatus properties propagate to the badge.
// Run: qmltestrunner -import ../overlay/app -input tst_status_badge.qml
import QtQuick 2.15
import QtTest 1.2
import "../overlay/app"

Item {
    id: window
    width: 1280
    height: 480

    QtObject {
        id: model
        property string state: "stable"
        property string version: "1.0.0"
        property string title: "STABLE"
        property string detail: "SW 1.0.0"
    }

    StatusBadge {
        id: badge
        anchors.fill: parent
        status: model
    }

    TestCase {
        name: "StatusBadge"
        when: windowShown

        function child(name) { return findChild(badge, name) }

        function set(state, title, detail) {
            model.state = state
            model.title = title
            model.detail = detail
        }

        function test_stable_is_neutral_green_without_strip() {
            set("stable", "STABLE", "SW 1.0.0")
            compare(child("statusTitle").text, "STABLE")
            compare(child("statusDetail").text, "SW 1.0.0")
            verify(Qt.colorEqual(child("statusTitle").color, "#3FB950"))
            verify(!child("statusStrip").visible)
            verify(!child("statusStripTop").visible)
            verify(Qt.colorEqual(child("statusPlate").border.color, "#30363D"))
        }

        function test_trial_is_cyan_with_strip() {
            set("trial", "OTA TRIAL", "SW 1.1.1")
            compare(child("statusDetail").text, "SW 1.1.1")
            verify(Qt.colorEqual(child("statusTitle").color, "#22D3EE"))
            verify(child("statusStrip").visible)
            verify(Qt.colorEqual(child("statusStrip").color, "#22D3EE"))
            verify(Qt.colorEqual(child("statusPlate").border.color, "#22D3EE"))
        }

        function test_restored_is_amber_with_strip() {
            set("restored", "RESTORED", "SW 1.0.0")
            verify(Qt.colorEqual(child("statusTitle").color, "#F5B841"))
            verify(child("statusStrip").visible)
        }

        function test_unknown_is_gray() {
            set("unknown", "SW STATUS", "—")
            verify(Qt.colorEqual(child("statusTitle").color, "#8B949E"))
            verify(!child("statusStrip").visible)
        }

        // The upstream WarningLightBar spans the bottom edge; its rightmost
        // lamp ends near x = 1146 at 1280x480 (measured on the real app in
        // every layout). The badge must stay in the free margin: x >= 1150.
        function test_badge_fits_free_margin_right_of_warning_lights() {
            set("trial", "OTA TRIAL", "SW 1.1.1")
            var plate = child("statusPlate")
            var p = plate.mapToItem(window, 0, 0)
            compare(p.x + plate.width, window.width - 6)
            compare(p.y + plate.height, window.height - 6)
            compare(plate.width, 124)
            compare(plate.height, 50)
            verify(p.x >= 1150)
        }

        function test_title_shrinks_to_fit_instead_of_eliding() {
            set("trial", "OTA TRIAL", "SW 1.1.1")
            compare(child("statusTitle").fontSizeMode, Text.HorizontalFit)
            compare(child("statusTitle").minimumPixelSize, 11)
            verify(child("statusTitle").font.pixelSize <= 16)
            compare(child("statusTitle").elide, Text.ElideNone)
        }

        // Handel Gothic (the dashboard font) draws "1" like "I"; the version
        // line uses upstream's bundled Arial Black so 1.0.0 vs 1.1.1 is legible.
        function test_version_uses_bundled_legible_font() {
            var loader = findChild(badge, "statusVersionFont")
            verify(loader !== null)
            compare(String(loader.source), "qrc:/fonts/ariblk.ttf")
        }

        function test_long_version_is_elided_not_widened() {
            set("trial", "OTA TRIAL", "SW 1.0.0-rc.1+build.1234567890")
            compare(child("statusPlate").width, 124)
            compare(child("statusDetail").elide, Text.ElideRight)
        }

        function test_trial_frames_screen_with_top_and_bottom_lines() {
            set("trial", "OTA TRIAL", "SW 1.1.1")
            verify(child("statusStripTop").visible)
            verify(child("statusStrip").visible)
            compare(child("statusStripTop").height, 4)
            compare(child("statusStripTop").width, window.width)
        }

        function test_markup_in_detail_is_not_interpreted() {
            set("trial", "OTA TRIAL", "<b>x</b>")
            compare(child("statusDetail").textFormat, Text.PlainText)
        }

        function test_null_status_renders_unknown() {
            badge.status = null
            compare(child("statusTitle").text, "SW STATUS")
            compare(child("statusDetail").text, "—")
            badge.status = model
        }

        function test_badge_never_takes_input() {
            verify(!badge.enabled)
        }
    }
}
