"""The digits and the arc must agree: on Pi2 the arc is painted late (about 100 ms, 400 ms under the map load)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

def test_digits_never_run_ahead_of_the_arc(tmp_path):
    """The arc is painted on another thread and is late when the Pi is busy; digits that update at once would then
    disagree with it. The digits therefore show the value of the arc that was actually painted."""
    qmlscene = shutil.which('qmlscene')
    if not qmlscene:
        pytest.skip('qmlscene is needed to read the digits')
    harness = tmp_path / 'digits.qml'
    harness.write_text('''
import QtQuick 2.15
import QtQuick.Window 2.15
import "file://%s"
Window { visible: true; width: 1280; height: 480; color: "#02040a"
  Item { anchors.fill: parent
    Cluster { id: c; objectName: "cluster"; transformOrigin: Item.TopLeft; scale: 0.667; speed: 20 } }
  Timer { interval: 1500; running: true; onTriggered: {
      var digits = c.children[0]
      for (var i = 0; i < c.data.length; ++i) if (c.data[i].objectName === "speedDigits") digits = c.data[i]
      var before = digits.text
      c.speed = 120                                   // the arc cannot have been painted yet
      console.log("RIGHT_AFTER:" + before + ">" + digits.text)
      settle.start() } }
  Timer { id: settle; interval: 1500; onTriggered: {
      for (var i = 0; i < c.data.length; ++i) if (c.data[i].objectName === "speedDigits") console.log("SETTLED:" + c.data[i].text)
      Qt.quit() } }
}
''' % (ROOT / 'dashboard/qt_cluster'))
    done = subprocess.run([qmlscene, str(harness)], capture_output=True, text=True, timeout=40,
                          env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_QUICK_BACKEND': 'software'})
    assert 'RIGHT_AFTER:20>20' in done.stderr, done.stderr      # still the old number: nothing painted for 120 yet
    assert 'SETTLED:120' in done.stderr, done.stderr             # and the new number once the arc caught up
