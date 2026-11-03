"""Live PC presentation: real Qt candidate + independent read-only Qt progress."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from capstone_ota.qt_verify import running_dashboard, deliver
from dashboard.qt_cluster.build import ROOT
from .qt_run import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scenario', choices=['mixed', 'attack', 'compatible'], default='mixed')
    args = parser.parse_args()
    if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        parser.error('a desktop or private Xvfb display is required')
    os.environ['CAPSTONE_QT_LIVE'] = '1'
    build_config = ROOT / '.demo/qt-build/runtime.json'
    if 'CAPSTONE_QT_PREFIX' not in os.environ and build_config.exists():
        os.environ['CAPSTONE_QT_PREFIX'] = json.loads(build_config.read_text())['qt_prefix']
    root = args.output.absolute() / (args.scenario + '-cluster')
    panel = subprocess.Popen([str(ROOT / 'dashboard/qt_progress/run'), '--root', str(root)])
    try:
        result = run(args.output, scenario=args.scenario)
        print(json.dumps({args.scenario: result[args.scenario]['result']}), flush=True)
        if result[args.scenario]['result'] in {'committed', 'rolled_back'}:
            # PC checks close their child Qt; reopen the accepted app for presentation.
            # This is a bench display, not proof of target systemd provisioning.
            with running_dashboard(root / 'current') as target:
                counter = 0
                while panel.poll() is None:
                    deliver(target, 650, counter % 256)
                    counter += 1
                    time.sleep(.1)
        else:
            # Refused recovery must never reopen V1 or claim service restoration.
            panel.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if panel.poll() is None:
            panel.terminate()
            try:
                panel.wait(timeout=2)
            except subprocess.TimeoutExpired:
                panel.kill(); panel.wait()


if __name__ == '__main__':
    main()
