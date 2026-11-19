"""MapView.qml loads offscreen, draws map data and reports sane route info (no QML errors)."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

UI = Path(__file__).parents[1] / 'dashboard/ivi/ui'

HARNESS = '''
import QtQuick 2.15
import "file://%(ui)s" as UI

Item {
    width: 1450; height: 540
    UI.MapView { id: mv; anchors.fill: parent }
    Component.onCompleted: {
        var x = new XMLHttpRequest()
        x.onreadystatechange = function () {
            if (x.readyState !== 4) return
            var d = JSON.parse(x.responseText)
            mv.nav = { x: %(x)s, y: %(y)s, heading: %(heading)s, speed: 30, distance: %(distance)s }
            mv.mapData = d
            script.start()
        }
        x.open("GET", "file://%(map)s"); x.send()
    }
    Timer { id: script; interval: 700
        onTriggered: {
            console.log("INFO " + JSON.stringify(mv.info) + " NAME " + mv.streetName)
            mv.headingUp = false
            mv.nav = { x: %(x)s + 5, y: %(y)s, heading: %(heading)s, speed: 30, distance: %(distance)s + 5 }
            done.start()
        }
    }
    Timer { id: done; interval: 700
        onTriggered: {
            console.log("DONE " + mv.layerReady)
            Qt.quit()
        }
    }
}
'''

# A 300 m square loop (1200 m): along the south leg, heading west, 200 m before the closing corner.
SQUARE = {'origin': [126.9779, 37.5663], 'attribution': '(c) OpenStreetMap contributors (ODbL)',
          'roads': [{'c': 'primary', 'p': [0, 0, 300, 0], 'n': 'Test Street'},
                    {'c': 'residential', 'p': [100, -80, 100, 80]},
                    {'c': 'motorway', 'p': [-200, 40, 500, 40]}],
          'route': {'p': [0, 0, 0, 300, 300, 300, 300, 0, 0, 0], 'length': 1200}}


def run_view(tmp_path, map_file, x, y, heading, distance):
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene (Qt 5.15) is needed to load the QML component')
    qml = tmp_path / 'harness.qml'
    qml.write_text(HARNESS % dict(ui=UI, map=map_file, x=x, y=y, heading=heading, distance=distance))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_XHR_ALLOW_FILE_READ='1')
    result = subprocess.run([qmlscene, str(qml)], env=env, capture_output=True, text=True, timeout=60)
    assert 'Error:' not in result.stderr, result.stderr
    assert not re.search(r'TypeError|ReferenceError|Unable to assign|is not defined|not a type', result.stderr), result.stderr
    assert result.returncode == 0, result.stderr
    assert 'DONE true' in result.stderr + result.stdout       # the road bitmap was painted
    return result.stderr + result.stdout


def test_square_loop_reports_remaining_distance_next_turn_and_street_name(tmp_path):
    map_file = tmp_path / 'map.json'
    map_file.write_text(json.dumps(SQUARE))
    out = run_view(tmp_path, map_file, x=200, y=0, heading=270, distance=1000)
    info = json.loads(re.search(r'INFO (\{.*\}) NAME', out).group(1))
    assert info['remaining'] == pytest.approx(200)
    assert info['turn']['distance'] == pytest.approx(200)      # the closing vertex is a right-hand corner
    assert info['turn']['angle'] == pytest.approx(90, abs=3)
    assert 'NAME Test Street' in out


@pytest.mark.skipif(not os.environ.get('IVI_MAP_JSON'), reason='set IVI_MAP_JSON to the real map.json pulled from the board')
def test_real_map_loads_without_errors(tmp_path):
    out = run_view(tmp_path, os.environ['IVI_MAP_JSON'], x=-59.2, y=0.1, heading=0, distance=0)
    info = json.loads(re.search(r'INFO (\{.*\}) NAME', out).group(1))
    assert info['remaining'] > 0 and info['turn'] is not None
