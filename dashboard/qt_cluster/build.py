#!/usr/bin/env python3
"""Build the fixed Qt cluster: a small CAN input/telemetry adapter plus the Cluster.qml screen."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build(output, qt_prefix=None):
    """Compile main.cpp + the cluster QML/fonts (all in capstone.qrc). Only Qt is needed; no third-party gauge code."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    qmake = str(Path(qt_prefix) / 'lib/qt5/bin/qmake') if qt_prefix else 'qmake'
    project = output / 'cluster.pro'
    project.write_text(f'''QT += quick qml network
CONFIG += c++17
TEMPLATE = app
TARGET = capstone-qt-cluster
INCLUDEPATH += "{Path(qt_prefix) / 'include' if qt_prefix else '/usr/include'}"
LIBS += -L"{Path(qt_prefix) / 'lib' / (platform.machine() + '-linux-gnu') if qt_prefix else '/usr/lib'}"
SOURCES += "{HERE}/main.cpp"
RESOURCES += "{HERE}/capstone.qrc"
''')
    with (output / 'build.log').open('w') as log:
        subprocess.run([qmake, str(project)], cwd=output, stdout=log, stderr=log, check=True)
        subprocess.run(['make', '-j2'], cwd=output, stdout=log, stderr=log, check=True)
    (output / 'runtime.json').write_text(json.dumps({'qt_prefix': str(qt_prefix) if qt_prefix else ''}))
    return output / 'capstone-qt-cluster'


def payload(binary, output):
    """Package the one fixed cluster; only IVI releases have product versions."""
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'bin').mkdir()
    (output / 'libexec').mkdir()
    shutil.copy2(binary, output / 'libexec/dashboard')
    shutil.copy2(HERE / 'app', output / 'bin/app')
    (output / 'bin/app').chmod(0o755)
    shutil.copy2(HERE / 'fonts/dmsans-OFL.txt', output / 'LICENSE.dmsans-OFL.txt')
    return output


def default_payload():
    binary = ROOT / '.demo/qt-build/capstone-qt-cluster'
    digest = hashlib.sha256(binary.read_bytes() + (HERE / 'app').read_bytes()).hexdigest()[:12]
    target = ROOT / '.demo/qt-payloads' / ('cluster-' + digest)
    if not target.exists():
        payload(binary, target)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=ROOT / '.demo/qt-build', type=Path)
    parser.add_argument('--qt-prefix', type=Path)
    args = parser.parse_args()
    print(build(args.output, args.qt_prefix))
