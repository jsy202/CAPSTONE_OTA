"""Feeds the single-monitor cockpit display (runs as root on the Cockpit board).

Every 0.5 s it writes one data.json combining independent sources: the real Qt cluster (live screen
capture + model state), the Verifier board's verdict, the IVI update progress/journal and the IVI app
status. Read-only with respect to all of them. A missing source is shown as missing, never as zero.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

EVENT_TEXT = {
    'download_and_verify': 'Update: downloading and verifying the signed release',
    'trial': 'Trial: new version installed, running under probation',
    'committed': 'Commit: release accepted (installed version is now final)',
    'recover': 'Rollback: restoring the previous version',
    'recovery_verified': 'Rollback: previous version checked against its recorded hash',
    'recovery_failed': 'Rollback FAILED: needs manual repair',
}
LOG_LIMIT = 14


def describe_event(name: str) -> str:
    return EVENT_TEXT.get(name, 'Device: ' + name)


class Feeder:
    def __init__(self, out_dir, cluster, pi3, ota, events, ivi, ivi_ui, clock=time.time, shot=None, tap=None):
        self.out, self.clock = Path(out_dir), clock
        self.cluster, self.pi3, self.ota, self.events, self.ivi, self.ivi_ui = cluster, pi3, ota, events, ivi, ivi_ui
        self.shot, self.tap = shot, tap
        self.log, self._seen, self._pi3_state, self._ivi_version = [], 0, None, None

    @staticmethod
    def _call(source):
        try:
            return source()
        except Exception:                    # a failing source is "unknown", never a made-up value
            return None

    def _add(self, now: float, text: str) -> None:
        self.log.append({'t': time.strftime('%H:%M:%S', time.localtime(now)), 'text': text})
        del self.log[:-LOG_LIMIT]

    def collect(self) -> dict:
        now = self.clock()
        cluster, pi3, ota, ivi = (self._call(s) for s in (self.cluster, self.pi3, self.ota, self.ivi))
        events = self._call(self.events) or []
        if len(events) < self._seen:
            self._seen = 0
        for event in events[self._seen:]:
            self._add(now, describe_event(event.get('event', '?')))
        self._seen = len(events)
        state = pi3.get('state') if pi3 else 'lost'
        if state != self._pi3_state:
            detail = '' if state in ('lost', 'observing') or not pi3.get('cls') else f" ({pi3['cls']})"
            self._add(now, f'Pi3 monitor: {"signal lost" if state == "lost" else state}{detail}')
            self._pi3_state = state
        version = ivi.get('version') if ivi else None
        if version and version != self._ivi_version:
            self._add(now, f'IVI running {version}')
            self._ivi_version = version
        return {'now': now, 'cluster': cluster, 'pi3': pi3, 'ota': ota, 'ivi': ivi, 'ivi_ui': self._call(self.ivi_ui),
                'shot': self._call(self.shot) if self.shot else None,
                'tap': self._call(self.tap) if self.tap else None, 'log': list(self.log)}

    def write(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        temporary = self.out / '.data.json.tmp'
        temporary.write_text(json.dumps(self.collect()))
        temporary.chmod(0o644)
        os.replace(temporary, self.out / 'data.json')


def read_json(path):
    return json.loads(Path(path).read_text())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=Path('/run/cockpit'))
    parser.add_argument('--cluster-socket', required=True)
    parser.add_argument('--cluster-capture', required=True, help='image the cluster writes on a capture request')
    parser.add_argument('--device-root', type=Path, default=Path('/opt/capstone-ivi/device'))
    parser.add_argument('--ivi-run', type=Path, default=Path('/run/capstone-ivi'))
    parser.add_argument('--pi3-ssh', required=True, help='JSON argv list for ssh to the Verifier board')
    parser.add_argument('--verdict-path', required=True)
    parser.add_argument('--channel', default='can0', help='CAN channel for the fast local delay tap')
    args = parser.parse_args(argv)
    ssh = json.loads(args.pi3_ssh)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.chmod(0o755)
    latest = {'verdict': None, 'at': 0.0}
    running = {'now': True}
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: running.update(now=False))

    def poll_pi3():                           # slow remote read in its own thread so the display loop never stalls
        while running['now']:
            try:
                done = subprocess.run([*ssh, 'cat ' + shlex.quote(args.verdict_path)], capture_output=True, text=True, timeout=8)
                latest.update(verdict=json.loads(done.stdout) if done.returncode == 0 else None, at=time.time())
            except (OSError, ValueError, subprocess.SubprocessError):
                latest.update(verdict=None, at=time.time())
            time.sleep(0.7)
    threading.Thread(target=poll_pi3, daemon=True).start()
    revision = {'n': 0}

    def cluster():
        from capstone_ota.qt_verify import socket_request
        report = socket_request(args.cluster_socket, {'op': 'capture', 'nonce': 'cockpit'})
        temporary = args.out_dir / '.cluster.tmp'
        shutil.copyfile(args.cluster_capture, temporary)
        temporary.chmod(0o644)
        os.replace(temporary, args.out_dir / 'cluster.bmp')
        revision['n'] += 1
        return {'valid': report['valid'], 'model_speed': report['model_speed'], 'rejected': report['rejected'],
                'rev': revision['n']}

    def pi3():
        return latest['verdict'] if time.time() - latest['at'] < 5 else None

    def ota():
        progress = read_json(args.device_root / 'progress.json')
        return {k: progress.get(k) for k in ('stage', 'version', 'candidate', 'slot_view', 'detail')}

    def events():
        return [json.loads(line) for line in (args.device_root / 'events.jsonl').read_text().splitlines() if line]

    def ivi_ui():
        slot = Path(os.path.realpath(args.device_root / 'current'))
        return ('file://' + str(slot / 'ui/Ivi.qml')) if (slot / 'ui/Ivi.qml').exists() else None

    def shot():                              # touching /run/cockpit/shot.request asks the display to save a screenshot
        request = args.out_dir / 'shot.request'
        return request.stat().st_mtime_ns if request.exists() else None

    from capstone_ota.monitor.tap import DelayTap, run as run_tap
    tap = DelayTap()
    threading.Thread(target=run_tap, args=(args.channel, tap, lambda: running['now']), daemon=True).start()

    feeder = Feeder(args.out_dir, cluster, pi3, ota, events, lambda: read_json(args.ivi_run / 'status.json'), ivi_ui,
                    shot=shot, tap=lambda: tap.snapshot(time.time()))
    while running['now']:
        started = time.monotonic()
        feeder.write()
        time.sleep(max(0.05, 0.5 - (time.monotonic() - started)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
