"""The digits and the arc must agree: on Pi2 the arc is painted late (about 100 ms, 400 ms under the map load)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

def lit_pixels(qmlscene, tmp_path, speeds):
    """Grab the cluster after each speed and count the bright arc pixels of the speed ring."""
    from PIL import Image
    shots = ', '.join('"%s"' % (tmp_path / f'shot{n}.png') for n in range(len(speeds)))
    harness = tmp_path / 'arc.qml'
    harness.write_text('''
import QtQuick 2.15
import QtQuick.Window 2.15
import "file://%s"
Window { visible: true; width: 1280; height: 480; color: "#02040a"
  property var speeds: %s
  property var shots: [%s]
  property int n: 0
  Item { id: host; anchors.fill: parent
    Cluster { id: c; transformOrigin: Item.TopLeft; scale: Math.min(parent.width / 1920, parent.height / 720); speed: 0 } }
  property bool grabbing: false
  Timer { interval: 700; running: true; repeat: true; onTriggered: {
      if (!grabbing) {                                  // set a speed ...
          if (n >= speeds.length) { stop(); quit.start(); return }
          c.speed = speeds[n]; grabbing = true
      } else {                                          // ... and look at it half a second later
          var k = n; n++; grabbing = false
          host.grabToImage(function(r) { r.saveToFile(shots[k]) }) } } }
  Timer { id: quit; interval: 1500; onTriggered: Qt.quit() }
}
''' % (ROOT / 'dashboard/qt_cluster', speeds, shots))
    subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=60,
                   env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'})
    counts = []
    for n in range(len(speeds)):
        image = Image.open(tmp_path / f'shot{n}.png').convert('RGB')
        width, height = image.size
        box = image.crop((0, 0, width // 3, height))               # the speed ring is the left third
        raw = box.tobytes()
        counts.append(sum(1 for i in range(0, len(raw), 3) if raw[i + 2] > 190 and raw[i + 1] > 130 and raw[i] < 150))
    return counts


def test_arc_follows_the_speed_both_up_and_down(tmp_path):
    """The arc must shrink when the speed falls (on Pi2 an old arc stayed on screen while the digits went to 0)."""
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed to look at the arc')
    up_high, down_low, still_zero, up_again = lit_pixels(qmlscene, tmp_path, [150, 30, 0, 150])
    assert up_high > 4 * down_low * 0.6, (up_high, down_low)        # 150 km/h lights about five times the arc of 30
    assert down_low > 0
    assert still_zero * 20 < up_high, (still_zero, up_high)         # at 0 km/h no arc is left behind
    assert abs(up_again - up_high) < up_high * 0.1, (up_again, up_high)
