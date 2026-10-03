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
    assert "Totals: 38 passed, 0 failed" in result.stdout
    assert "Totals: 14 passed, 0 failed" in result.stdout


# --- reproducible patch application (Task 6) -------------------------------

import os
import shutil
import subprocess

import pytest

PATCH = CUSTOM / "patches" / "0001-capstone-ota-status-badge.patch"
APPLY = CUSTOM / "apply-customization.sh"
PINNED = "793452919127065536bcb7a08f98838fa963d75e"


def test_patch_touches_only_the_four_allowed_upstream_files():
    targets = sorted(line.split()[1].split("\t")[0] for line in PATCH.read_text().splitlines()
                     if line.startswith("+++ "))
    assert targets == ["b/app/app.pro", "b/app/main.qml", "b/app/qml.qrc", "b/app/src/main.cpp"]


def test_patch_wires_model_badge_resource_and_sources():
    added = "\n".join(line for line in PATCH.read_text().splitlines() if line.startswith("+"))
    for needle in ('setContextProperty("capstoneStatus"', "#include <app_status.h>", "StatusBadge {",
                   "<file>StatusBadge.qml</file>", "src/capstone/app_status.cpp",
                   "inc/capstone/app_status.h", "INCLUDEPATH += inc/capstone"):
        assert needle in added, needle
    removed = [line for line in PATCH.read_text().splitlines()
               if line.startswith("-") and not line.startswith("---")]
    assert removed == [], "the patch must only add lines to upstream files"


def test_apply_script_is_strict_and_pinned():
    script = APPLY.read_text()
    assert APPLY.stat().st_mode & 0o111
    assert "set -euo pipefail" in script and "patch -p1 --forward" in script and "--dry-run" in script
    assert PINNED in script


def test_apply_refuses_a_tree_that_is_not_an_import(tmp_path):
    (tmp_path / "app").mkdir()
    result = subprocess.run([str(APPLY), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / ".capstone-customization-applied").exists()
    assert not (tmp_path / "app" / "StatusBadge.qml").exists()


def test_patch_applies_to_pinned_upstream_and_refuses_reapplication(tmp_path):
    source = os.environ.get("CAPSTONE_UPSTREAM_DIR")
    if not source:
        pytest.skip("CAPSTONE_UPSTREAM_DIR not set: pinned upstream import unavailable")
    tree = tmp_path / "VolvoDigitalDashModels"
    shutil.copytree(source, tree, symlinks=True)
    first = subprocess.run([str(APPLY), str(tree)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert PINNED in (tree / ".capstone-customization-applied").read_text()
    assert (tree / "app" / "StatusBadge.qml").read_text() == (OVERLAY / "StatusBadge.qml").read_text()
    assert (tree / "app" / "src" / "capstone" / "app_status.cpp").is_file()
    assert "StatusBadge {" in (tree / "app" / "main.qml").read_text()
    second = subprocess.run([str(APPLY), str(tree)], capture_output=True, text=True)
    assert second.returncode != 0 and "already applied" in second.stderr


# --- desktop capture tool: clearance measurement ----------------------------

def _capture_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "capture_dashboard_states", CUSTOM / "qt-tests" / "capture_dashboard_states.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame_with_content_at(x_right):
    width, height = 1280, 480
    rgb = bytearray(width * height * 3)
    y = height - 30
    for x in range(x_right - 60, x_right + 1):
        rgb[(y * width + x) * 3:(y * width + x) * 3 + 3] = b"\xff\xa5\x00"  # amber lamp
    return width, height, bytes(rgb)


def test_clearance_passes_when_last_lamp_ends_left_of_plate():
    result = _capture_module().badge_clearance(*_frame_with_content_at(1146))
    assert result["plate_left"] == 1150 and result["gap_px"] == 3 and result["overlap"] is False


def test_clearance_fails_when_upstream_content_touches_plate():
    result = _capture_module().badge_clearance(*_frame_with_content_at(1149))
    assert result["overlap"] is True


def test_reviewed_decorative_overlaps_are_explicit_and_few():
    module = _capture_module()
    assert set(module.REVIEWED_DECORATIVE) <= set(module.SCREENS)
    assert len(module.REVIEWED_DECORATIVE) <= 1


def _blank(width=1280, height=480):
    return bytearray(width * height * 3)


def _paint(rgb, x0, y0, x1, y1, width=1280, color=b"\x30\x30\x30"):
    for y in range(y0, y1):
        for x in range(x0, x1):
            rgb[(y * width + x) * 3:(y * width + x) * 3 + 3] = color


def test_covered_content_counts_baseline_pixels_under_plate_and_strips():
    module = _capture_module()
    rgb = _blank()
    assert module.covered_content(1280, 480, bytes(rgb)) == {"plate_px": 0, "strip_px": 0}
    _paint(rgb, 1200, 440, 1210, 450)       # dim unlit lamp under the plate area
    _paint(rgb, 100, 0, 110, 2)             # content touching the top edge
    result = module.covered_content(1280, 480, bytes(rgb))
    assert result == {"plate_px": 100, "strip_px": 20}


def test_capture_records_hashes_of_the_badge_sources():
    module = _capture_module()
    hashes = module.source_hashes()
    import hashlib
    assert hashes["StatusBadge.qml"] == hashlib.sha256((OVERLAY / "StatusBadge.qml").read_bytes()).hexdigest()
    assert set(hashes) == {"StatusBadge.qml", "app_status.cpp"}


# --- application contract wiring (zonal application contracts) --------------

def _added_lines():
    return [line[1:] for line in PATCH.read_text().splitlines() if line.startswith("+") and not line.startswith("+++")]


def test_patch_wires_cluster_ipc_server_into_the_real_context():
    added = "\n".join(_added_lines())
    for needle in ("#include <cluster_ipc_server.h>", "#include <context_model_access.h>",
                   "new ClusterIpcServer(new ContextModelAccess(ctxt)", "listenOrWarn()",
                   "QT += network", "src/capstone/cluster_ipc_server.cpp", "src/capstone/cluster_signals.cpp"):
        assert needle in added, needle


def test_fault_define_exists_only_behind_explicit_qmake_variable():
    added = _added_lines()
    lines = [l.strip() for l in added]
    define = next(i for i, l in enumerate(lines) if "DEFINES += CAPSTONE_FAULT_SPEED_DIVISOR" in l)
    assert lines[define - 1].startswith("!isEmpty(CAPSTONE_FAULT_SPEED_DIVISOR)")
    assert sum("CAPSTONE_FAULT_SPEED_DIVISOR=" in l and "DEFINES" in l for l in lines) == 1
    sources = (OVERLAY / "src" / "capstone" / "cluster_signals.cpp").read_text()
    assert "#ifdef CAPSTONE_FAULT_SPEED_DIVISOR" in sources and "static_assert(CAPSTONE_FAULT_SPEED_DIVISOR >= 2" in sources
    server = (OVERLAY / "src" / "capstone" / "cluster_ipc_server.cpp").read_text()
    marker = server.index("DEMO/TEST FAULT INJECTION BUILD")
    assert server.rfind("#ifdef CAPSTONE_FAULT_SPEED_DIVISOR", 0, marker) > server.rfind("#endif", 0, marker)


def _binary(tmp_path, name, marker=False):
    path = tmp_path / name
    path.write_bytes(b"\x7fELF" + (b"DEMO/TEST FAULT INJECTION BUILD" if marker else b"") + b"\0" * 16)
    path.chmod(0o755)
    return path


def test_normal_payload_refuses_a_fault_injection_binary(tmp_path):
    make = DASH / "make-payload.sh"
    ok = subprocess.run([str(make), str(_binary(tmp_path, "good")), str(tmp_path / "p1")], capture_output=True)
    assert ok.returncode == 0
    bad = subprocess.run([str(make), str(_binary(tmp_path, "bad", marker=True)), str(tmp_path / "p2")],
                         capture_output=True, text=True)
    assert bad.returncode != 0 and "FAULT" in bad.stderr


def test_fault_payload_requires_marker_and_is_labelled(tmp_path):
    make = CUSTOM / "make-fault-payload.sh"
    refused = subprocess.run([str(make), str(_binary(tmp_path, "good")), str(tmp_path / "p1")], capture_output=True)
    assert refused.returncode != 0
    done = subprocess.run([str(make), str(_binary(tmp_path, "bad", marker=True)), str(tmp_path / "p2")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "DEMO/TEST FAULT INJECTION ONLY" in (tmp_path / "p2" / "FAULT-INJECTION-ONLY").read_text()


def test_qt_application_contract_tests_pass_when_qt_available():
    qt = os.environ.get("CAPSTONE_QT_DIR")
    if not qt:
        pytest.skip("CAPSTONE_QT_DIR not set: Qt toolchain unavailable")
    result = subprocess.run([str(CUSTOM / "qt-tests/run-qt-tests.sh"), qt], capture_output=True, text=True, timeout=900)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    totals = [line for line in result.stdout.splitlines() if line.startswith("Totals:")]
    assert len(totals) == 5, totals   # AppStatus, signals, signals (fault build), IPC server, QML
    assert all(", 0 failed," in line for line in totals)
