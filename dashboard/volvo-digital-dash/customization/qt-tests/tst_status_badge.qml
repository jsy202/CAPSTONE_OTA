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
        property string title: "● STABLE"
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
            set("stable", "● STABLE", "SW 1.0.0")
            compare(child("statusTitle").text, "● STABLE")
            compare(child("statusDetail").text, "SW 1.0.0")
            verify(Qt.colorEqual(child("statusTitle").color, "#3FB950"))
            verify(!child("statusStrip").visible)
            verify(Qt.colorEqual(child("statusPlate").border.color, "#30363D"))
        }

        function test_trial_is_cyan_with_strip() {
            set("trial", "● OTA TRIAL", "SW 1.1.1")
            compare(child("statusDetail").text, "SW 1.1.1")
            verify(Qt.colorEqual(child("statusTitle").color, "#22D3EE"))
            verify(child("statusStrip").visible)
            verify(Qt.colorEqual(child("statusStrip").color, "#22D3EE"))
            verify(Qt.colorEqual(child("statusPlate").border.color, "#22D3EE"))
        }

        function test_restored_is_amber_with_strip() {
            set("restored", "↺ ROLLBACK COMPLETE", "RESTORED SW 1.0.0")
            verify(Qt.colorEqual(child("statusTitle").color, "#F5B841"))
            verify(child("statusStrip").visible)
        }

        function test_unknown_is_gray() {
            set("unknown", "SW STATUS", "—")
            verify(Qt.colorEqual(child("statusTitle").color, "#8B949E"))
            verify(!child("statusStrip").visible)
        }

        function test_badge_stays_in_bottom_right_corner() {
            set("trial", "● OTA TRIAL", "SW 1.1.1")
            var plate = child("statusPlate")
            var p = plate.mapToItem(window, 0, 0)
            compare(p.x + plate.width, window.width - 12)
            compare(p.y + plate.height, window.height - 12)
            verify(plate.width < window.width / 4)
        }

        function test_markup_in_detail_is_not_interpreted() {
            set("trial", "● OTA TRIAL", "<b>x</b>")
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
