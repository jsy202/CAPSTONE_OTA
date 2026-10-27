#!/usr/bin/env python3
"""Build a small input/telemetry adapter around pinned, unmodified Volvo models/QML."""
import argparse
import hashlib
import json
import platform
import re
from pathlib import Path
import shutil
import subprocess

PIN = '793452919127065536bcb7a08f98838fa963d75e'
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build(source, output, qt_prefix=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != PIN:
        raise ValueError('Volvo source checkout must be pinned to ' + PIN)
    output.mkdir(parents=True, exist_ok=True)
    imported = output / 'upstream'
    if not imported.exists():
        imported.mkdir()
        archive = output / 'upstream.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git', '-C', str(source), 'archive', PIN,
                            'QtDash/VolvoDigitalDashModels/app'], stdout=stream, check=True)
        subprocess.run(['tar', '-xf', str(archive), '-C', str(imported)], check=True)
        archive.unlink()
    app = imported / 'QtDash/VolvoDigitalDashModels/app'
    qml = app / 'main.qml'
    text = re.sub(r'property var msg: \[.*?\]', 'property var msg: ["CAPSTONE OTA"]',
                  qml.read_text(), count=1, flags=re.S)
    if 'property bool capstoneGaugeReady' not in text:
        text = text.replace('property url initialScreen:',
                            'property bool capstoneGaugeReady: currentGauge.status === Loader.Ready && currentGauge.visible\n    property url initialScreen:')
    text = text.replace('interval: 2000', 'interval: 1')  # No original joke/splash delay in the OTA adapter.
    text = text.replace('++index > rootWindow.msg.length', '++index >= rootWindow.msg.length')
    start, end = text.index('id: currentGauge'), text.index('id: bootTimer')
    text = text[:start] + text[start:end].replace('target: item', 'target: currentGauge.item') + text[end:]
    qml.write_text(text.replace('qsTr("Lolvo")', 'qsTr("CAPSTONE OTA Cluster")'))
    delegate = app / 'SpeedoDelegate.qml'
    text = delegate.read_text()
    if 'capstoneSpeedNeedle' not in text:
        delegate.write_text(text.replace('onLoaded: {', 'onLoaded: {\n                item.objectName = "capstoneSpeedNeedle";', 1))
    qmake = str(Path(qt_prefix) / 'lib/qt5/bin/qmake') if qt_prefix else 'qmake'
    project = output / 'cluster.pro'
    project.write_text(f'''QT += quick qml network
CONFIG += c++17
TEMPLATE = app
TARGET = capstone-qt-cluster
INCLUDEPATH += "{app}/inc/model" "{app}/inc/utils"
INCLUDEPATH += "{Path(qt_prefix) / 'include' if qt_prefix else '/usr/include'}"
LIBS += -L"{Path(qt_prefix) / 'lib' / (platform.machine() + '-linux-gnu') if qt_prefix else '/usr/lib'}"
SOURCES += "{HERE}/main.cpp" $$files("{app}/src/model/*.cpp")
HEADERS += $$files("{app}/inc/model/*.h") "{app}/inc/utils/key_press_emitter.h"
RESOURCES += "{app}/qml.qrc" "{HERE}/capstone.qrc"
''')
    with (output / 'build.log').open('w') as log:
        subprocess.run([qmake, str(project)], cwd=output, stdout=log, stderr=log, check=True)
        subprocess.run(['make', '-j2'], cwd=output, stdout=log, stderr=log, check=True)
    (output / 'runtime.json').write_text(json.dumps({'qt_prefix': str(qt_prefix) if qt_prefix else ''}))
    return output / 'capstone-qt-cluster'


def payload(binary, output, policy, version='DEV'):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'bin').mkdir()
    (output / 'libexec').mkdir()
    shutil.copy2(binary, output / 'libexec/dashboard')
    shutil.copy2(HERE / 'app', output / 'bin/app')
    (output / 'bin/app').chmod(0o755)
    (output / 'cluster.json').write_text(json.dumps({'policy': policy, 'version': version}))
    shutil.copy2(HERE / 'LICENSE.upstream', output / 'LICENSE.upstream')
    return output


def default_payload(policy):
    binary = ROOT / '.demo/qt-build/capstone-qt-cluster'
    digest = hashlib.sha256(binary.read_bytes() + (HERE / 'app').read_bytes()).hexdigest()[:12]
    target = ROOT / '.demo/qt-payloads' / (policy + '-' + digest)
    if not target.exists():
        payload(binary, target, policy, 'V2' if policy == 'source_counter' else 'V3')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', default=ROOT / '.demo/qt-build', type=Path)
    parser.add_argument('--qt-prefix', type=Path)
    args = parser.parse_args()
    print(build(args.source, args.output, args.qt_prefix))
