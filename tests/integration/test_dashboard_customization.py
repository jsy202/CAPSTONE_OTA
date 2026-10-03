"""Static interface checks of the Qt customization (runs without Qt)."""
import re
from pathlib import Path


DASH = Path(__file__).parents[2] / "dashboard" / "volvo-digital-dash"
CUSTOM = DASH / "customization"
OVERLAY = CUSTOM / "overlay" / "app"


def test_overlay_and_qt_test_files_exist():
    for path in (OVERLAY / "inc/capstone/app_status.h", OVERLAY / "src/capstone/app_status.cpp",
                 OVERLAY / "StatusBadge.qml", CUSTOM / "qt-tests/app_status_test.pro",
                 CUSTOM / "qt-tests/app_status_test.cpp"):
        assert path.is_file(), path


def test_badge_uses_spec_colors_never_red_and_never_takes_input():
    qml = (OVERLAY / "StatusBadge.qml").read_text()
    for color in ("#3FB950", "#22D3EE", "#F5B841", "#8B949E"):
        assert color in qml, color
    assert not re.search(r'"red"|#FF0000|#F00\b|#ff0000', qml, re.IGNORECASE)
    assert "enabled: false" in qml


def test_badge_and_model_hard_code_no_version_or_state_value():
    qml = (OVERLAY / "StatusBadge.qml").read_text()
    assert not re.search(r"\d+\.\d+\.\d+", qml)
    cpp = (OVERLAY / "src/capstone/app_status.cpp").read_text()
    assert not re.search(r'"\d+\.\d+\.\d+"', cpp)


def test_model_pins_source_path_limits_and_timing():
    cpp = (OVERLAY / "src/capstone/app_status.cpp").read_text()
    for literal in ("CAPSTONE_APP_STATUS_FILE", "/run/capstone-ota-ui/digital-cluster.json",
                    "CAPSTONE_APP_STATE", "CAPSTONE_APP_VERSION", "4096", "15000", "1000"):
        assert literal in cpp, literal


def test_qml_component_test_and_runner_exist():
    runner = CUSTOM / "qt-tests/run-qt-tests.sh"
    assert runner.is_file() and runner.stat().st_mode & 0o111
    assert "set -euo pipefail" in runner.read_text()
    assert (CUSTOM / "qt-tests/tst_status_badge.qml").is_file()


def test_qt_unit_and_component_tests_pass_when_qt_available():
    import os
    import subprocess

    import pytest

    qt = os.environ.get("CAPSTONE_QT_DIR")
    if not qt:
        pytest.skip("CAPSTONE_QT_DIR not set: Qt toolchain unavailable")
    result = subprocess.run([str(CUSTOM / "qt-tests/run-qt-tests.sh"), qt],
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert "Totals: 33 passed, 0 failed" in result.stdout
    assert "Totals: 10 passed, 0 failed" in result.stdout
