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
import signal
import subprocess
import threading
import time
from pathlib import Path

EVENT_TEXT = {
    'download_and_verify': '업데이트: 서명된 릴리스를 내려받는 중',
    'trial': '시운전: 새 버전 설치, 시험 중',
    'committed': '확정: 새 버전 최종 승인',
    'recover': '롤백: 이전 버전으로 되돌리는 중',
    'recovery_verified': '롤백: 이전 버전 검사 통과',
    'recovery_failed': '롤백 실패: 수동 점검 필요',
    'service_stopped_after_recovery_failure': '보호: 실패한 앱을 중지함',
    'repair_started': '복원: 승인된 릴리스를 다시 설치하는 중',
    'repair_installed': '복원: 설치 완료',
    'repair_rejected': '복원: 거부됨',
}
CLASS_TEXT = {'freeze': '응답 멈춤', 'delay': '응답 지연', 'input_anomaly': '입력 이상', 'normal': '정상'}
MONITOR_TEXT = {'lost': '신호 끊김', 'observing': '정상 관측', 'suspect': '징후 포착', 'confirmed': '한계 초과 확정'}
LOG_LIMIT = 14


def resolve_ivi_ui(current_slot, preview_ui=None):
    """The IVI screen the cockpit shows: the installed slot's own UI, or (preview only) a directory given on the command line."""
    if preview_ui is not None:
        qml = Path(preview_ui) / 'Ivi.qml'
        return 'file://' + str(qml) if qml.exists() else None
    slot = Path(os.path.realpath(current_slot))
    return ('file://' + str(slot / 'ui/Ivi.qml')) if (slot / 'ui/Ivi.qml').exists() else None


def describe_event(name: str) -> str:
    return EVENT_TEXT.get(name, '장치: ' + name)


class Feeder:
    def __init__(self, out_dir, cluster, pi3, ota, events, ivi, ivi_ui, clock=time.time, shot=None, tap=None, extra=None, labels=None,
                 recheck_s=0):
        self.out, self.clock = Path(out_dir), clock
        self.cluster, self.pi3, self.ota, self.events, self.ivi, self.ivi_ui = cluster, pi3, ota, events, ivi, ivi_ui
        self._log_n = 0
        self.recheck_s = recheck_s       # how long the device watches a restored image; the "확인" gauge counts against it
        self.labels = dict(labels or {})   # release version -> product name (display only)
        self.shot, self.tap, self.extra, self._extra_seen = shot, tap, extra, 0
        self.log, self._seen, self._pi3_state, self._ivi_version = [], 0, None, None

    @staticmethod
    def _call(source):
        try:
            return source()
        except Exception:                    # a failing source is "unknown", never a made-up value
            return None

    def _push(self, t: str, text: str) -> None:
        # `n` only ever grows: the list is trimmed to LOG_LIMIT rows, so the screen finds new rows by this number
        # (counting entries stops working once the list is full, and later alerts were silently lost).
        self._log_n += 1
        self.log.append({'n': self._log_n, 't': t, 'text': text})
        del self.log[:-LOG_LIMIT]

    def _add(self, now: float, text: str) -> None:
        self._push(time.strftime('%H:%M:%S', time.localtime(now)), text)

    def collect(self) -> dict:
        now = self.clock()
        cluster, pi3, ota, ivi = (self._call(s) for s in (self.cluster, self.pi3, self.ota, self.ivi))
        events = self._call(self.events) or []
        if len(events) < self._seen:
            self._seen = 0
        for event in events[self._seen:]:
            self._add(now, describe_event(event.get('event', '?')))
        self._seen = len(events)
        # Lines another component of the vehicle reported (e.g. the diagnostic port opening/closing). Shown
        # with their own time; display only.
        rows = (self._call(self.extra) if self.extra else None) or []
        if len(rows) < self._extra_seen:
            self._extra_seen = 0
        for row in rows[self._extra_seen:]:
            if isinstance(row, dict) and row.get('text'):
                self._push(str(row.get('t') or time.strftime('%H:%M:%S', time.localtime(now))), str(row['text']))
        self._extra_seen = len(rows)
        state = pi3.get('state') if pi3 else 'lost'
        if state != self._pi3_state:
            detail = '' if state in ('lost', 'observing') or not pi3.get('cls') else f" ({CLASS_TEXT.get(pi3['cls'], '알 수 없음')})"
            label = MONITOR_TEXT.get(state, state)
            self._add(now, f'모니터: {label}{detail}')
            self._pi3_state = state
        version = ivi.get('version') if ivi else None
        if version and version != self._ivi_version:
            self._add(now, f'화면 앱 {version} 실행 중')
            self._ivi_version = version
        return {'now': now, 'cluster': cluster, 'pi3': pi3, 'ota': ota, 'ivi': ivi, 'ivi_ui': self._call(self.ivi_ui),
                'shot': self._call(self.shot) if self.shot else None,
                'tap': self._call(self.tap) if self.tap else None, 'log': list(self.log),
                'version_labels': self.labels, 'recheck_s': self.recheck_s}

    def write(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        temporary = self.out / '.data.json.tmp'
        temporary.write_text(json.dumps(self.collect()))
        temporary.chmod(0o644)
        os.replace(temporary, self.out / 'data.json')


def read_json(path):
    return json.loads(Path(path).read_text())


def read_ivi_status(path, now=None):
    """The IVI's status.json plus signs of life: the files outlive the process, so also report how old they are,
    how old its 0.2 s nav.json is (when it has one) and whether its process still exists."""
    path = Path(path)
    moment = time.time() if now is None else now
    data = read_json(path)
    data['age_s'] = round(moment - path.stat().st_mtime, 2)
    nav = path.with_name('nav.json')
    if nav.exists():
        data['nav_age_s'] = round(moment - nav.stat().st_mtime, 2)
    pid = data.get('pid')
    if isinstance(pid, int):
        data['alive'] = os.path.exists(f'/proc/{pid}')
    return data


def session_view(progress, started, slack=1.0):
    """The device keeps its last progress on disk, so a fresh screen would draw the previous drill's finished rollback as
    if it were happening now. Progress written before this screen started is history: keep the version and slots but
    report an idle device, stage 'idle' (no gauge, no replayed alerts; 'committed' would draw a finished install)."""
    if (progress.get('updated') or 0.0) >= started - slack:
        return progress
    return {'stage': 'idle', 'version': progress.get('version'), 'slot_view': progress.get('slot_view')}


def capture_cluster(socket_path, display, out_dir, revision):
    from PIL import ImageGrab
    from capstone_ota.qt_verify import socket_request
    # Qt's grabWindow renders on its CAN-processing thread. Read X11's existing pixels instead.
    report = socket_request(socket_path, {'op': 'snapshot', 'nonce': 'cockpit'})
    temporary = Path(out_dir) / '.cluster.tmp'
    ImageGrab.grab(xdisplay=display).save(temporary, format='BMP')
    temporary.chmod(0o644)
    os.replace(temporary, Path(out_dir) / 'cluster.bmp')
    return {'valid': report['valid'], 'model_speed': report['model_speed'],
            'rejected': report['rejected'], 'rev': revision}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, default=Path('/run/cockpit'))
    parser.add_argument('--cluster-socket', required=True)
    parser.add_argument('--cluster-capture', help='Legacy argument; captures now read the external X11 framebuffer')
    parser.add_argument('--cluster-display', default=':93', help='Private cluster X11 display; requires Pillow/XCB')
    parser.add_argument('--device-root', type=Path, default=Path('/opt/capstone-ivi/device'))
    parser.add_argument('--ivi-run', type=Path, default=Path('/run/capstone-ivi'))
    parser.add_argument('--recheck-seconds', type=float, default=0.0, help='length of the post-rollback watch, for the "확인" gauge')
    parser.add_argument('--pi3-ssh', required=True, help='JSON argv list for ssh to the Verifier board')
    parser.add_argument('--verdict-path', required=True)
    parser.add_argument('--ivi-ui', type=Path, help='PREVIEW ONLY: show this ui directory instead of the installed slot')
    parser.add_argument('--version-labels', default='{}', help='JSON {release version: name} shown instead of version numbers')
    parser.add_argument('--interval', type=float, default=0.5, help='seconds between data.json writes (and cluster captures)')
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
        revision['n'] += 1
        return capture_cluster(args.cluster_socket, args.cluster_display, args.out_dir, revision['n'])

    def pi3():
        return latest['verdict'] if time.time() - latest['at'] < 5 else None

    session_start = time.time()      # not `started`: the loop below reuses that name for its own timer

    def ota():
        progress = session_view(read_json(args.device_root / 'progress.json'), session_start)
        return {k: progress.get(k) for k in ('stage', 'version', 'candidate', 'slot_view', 'detail',
                                           'timeline_id', 'timeline', 'received', 'total', 'work', 'updated')}

    def events():
        return [json.loads(line) for line in (args.device_root / 'events.jsonl').read_text().splitlines() if line]

    def ivi_ui():
        return resolve_ivi_ui(args.device_root / 'current', args.ivi_ui)

    def shot():                              # touching /run/cockpit/shot.request asks the display to save a screenshot
        request = args.out_dir / 'shot.request'
        return request.stat().st_mtime_ns if request.exists() else None

    from capstone_ota.monitor.tap import DelayTap, run as run_tap
    tap = DelayTap()
    threading.Thread(target=run_tap, args=(args.channel, tap, lambda: running['now']), daemon=True).start()

    def extra():
        path = args.out_dir / 'extra.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []

    feeder = Feeder(args.out_dir, cluster, pi3, ota, events, lambda: read_ivi_status(args.ivi_run / 'status.json'), ivi_ui, labels=json.loads(args.version_labels), recheck_s=args.recheck_seconds,
                    shot=shot, tap=lambda: tap.snapshot(time.time()), extra=extra)
    while running['now']:
        started = time.monotonic()
        feeder.write()
        time.sleep(max(0.05, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
