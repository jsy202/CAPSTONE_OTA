import os
from pathlib import Path
import shutil
import subprocess

import pytest

UI = Path(__file__).parents[1] / "dashboard/ivi/ui"
NAMES = [
    "search",
    "search-small",
    "bell",
    "lte",
    "volume",
    "bluetooth",
    "profile",
    "car",
    "seat-heat-left",
    "seat-heat-right",
    "defrost-front",
    "defrost-rear",
    "fan",
    "fan-speed",
    "air-direction",
    "recirculation",
    "media",
    "phone",
    "navigation",
    "apps",
    "manual",
    "radio",
    "home",
    "building",
    "star",
    "fuel",
    "parking",
    "compass-2d",
    "map-auto",
    "traffic-light",
    "settings",
    "chevron-left",
    "chevron-right",
    "chevron-up",
    "lock-car",
    "voice",
]
TILES = ["media", "phone", "navigation", "fan", "manual", "radio", "apps"]


def _run(tmp_path, body):
    qmlscene = shutil.which("qmlscene")
    if not qmlscene:
        pytest.skip("qmlscene is needed to load the icon components")
    harness = tmp_path / "IconCheck.qml"
    harness.write_text(
        """import QtQuick 2.15
import "%s"
Item {
    width: 600; height: 600
    %s
    Timer { interval: 700; running: true; onTriggered: { console.log("ICON_DONE"); Qt.quit() } }
}
"""
        % (UI.as_uri(), body)
    )
    done = subprocess.run(
        [qmlscene, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "QT_QUICK_BACKEND": "software",
        },
    )
    assert done.returncode == 0, done.stderr
    assert "ICON_DONE" in done.stderr, done.stderr
    for bad in (
        "Error:",
        "ReferenceError",
        "TypeError",
        "unknown name",
        "is not a type",
    ):
        assert bad not in done.stderr, done.stderr


def test_every_icon_name_paints_without_qml_errors(tmp_path):
    body = """Repeater {
        model: %s
        Icon { x: (index %% 8) * 70; y: Math.floor(index / 8) * 70; name: modelData; size: 64 }
    }""" % repr(NAMES)
    _run(tmp_path, body)


def test_dock_tiles_load(tmp_path):
    body = """Repeater {
        model: %s
        DockTile { x: index * 90; kind: modelData; size: 84 }
    }""" % repr(TILES)
    _run(tmp_path, body)
