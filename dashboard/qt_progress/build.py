"""Build the independent Qt monitor with the same Qt toolchain as the dashboard."""
import argparse
import json
from pathlib import Path
import platform
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def build(qt_prefix=None):
    output = ROOT / '.demo/progress-build'
    output.mkdir(parents=True, exist_ok=True)
    prefix = Path(qt_prefix) if qt_prefix else None
    project = output / 'progress.pro'
    project.write_text(f'''QT += quick qml
CONFIG += c++17
TEMPLATE = app
TARGET = capstone-ota-progress
INCLUDEPATH += "{prefix / 'include' if prefix else '/usr/include'}"
LIBS += -L"{prefix / 'lib' / (platform.machine() + '-linux-gnu') if prefix else '/usr/lib'}"
SOURCES += "{HERE}/main.cpp"
RESOURCES += "{HERE}/progress.qrc"
''')
    qmake = str(prefix / 'lib/qt5/bin/qmake') if prefix else 'qmake'
    with (output / 'build.log').open('w') as log:
        subprocess.run([qmake, str(project)], cwd=output, stdout=log, stderr=log, check=True)
        subprocess.run(['make', '-j2'], cwd=output, stdout=log, stderr=log, check=True)
    (output / 'runtime.json').write_text(json.dumps({'qt_prefix': str(prefix) if prefix else ''}))
    return output / 'capstone-ota-progress'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qt-prefix', type=Path)
    print(build(parser.parse_args().qt_prefix))
