#!/usr/bin/env python3
"""Mock IVI head unit used to study cross-function interference on a shared Cockpit board.

While the map screen is open (flag file), tile-decode workers burn CPU. The release configuration
decides how many workers run and at which scheduling priority. This is a test application: the
'decode' is a busy loop standing in for CPU-bound tile decoding/rendering, not a real IVI.
"""
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nav import Route, position  # noqa: E402  (shipped next to this file in the release payload)

ROOT = Path(__file__).resolve().parent.parent
PRIORITIES = ('normal', 'rr10')
CAN_FRAME = struct.Struct('=IB3x8s')


def open_can(channel):
    """Receive-only SocketCAN socket for the vehicle speed (0x200); None when unavailable."""
    if not channel:
        return None
    try:
        bus = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        bus.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack('=II', 0x200, 0x7FF))
        bus.bind((channel,))
        bus.setblocking(False)
        return bus
    except OSError:
        return None


def load_route():
    try:
        data = json.loads((ROOT / 'map.json').read_text())
        return Route.from_flat(data['route']['p'])
    except (OSError, ValueError, KeyError):
        return None


def load_config(path=None):
    config = json.loads(Path(path or ROOT / 'ivi.json').read_text())
    workers = config.get('map_workers') if isinstance(config, dict) else None
    if (not isinstance(config, dict) or set(config) != {'version', 'map_enabled', 'map_workers', 'map_priority'}
            or not isinstance(config['version'], str) or type(config['map_enabled']) is not bool
            or config['map_priority'] not in PRIORITIES
            or not (workers == 'cores' or (type(workers) is int and 0 <= workers <= 64))):
        raise ValueError('invalid IVI configuration')
    return config


def worker_count(config):
    return (os.cpu_count() or 1) if config['map_workers'] == 'cores' else config['map_workers']


def set_priority(priority):
    if priority == 'rr10':
        try:
            os.sched_setscheduler(0, os.SCHED_RR, os.sched_param(10))
        except PermissionError:
            print('realtime priority not granted; running at normal priority', file=sys.stderr, flush=True)
            return False
    return True


def worker(priority):
    set_priority(priority)
    value = 1
    while True:
        for _ in range(200000):
            value = (value * 1103515245 + 12345) & 0xFFFFFFFF


def atomic_json(path, data):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)


def main(argv):
    if len(argv) >= 3 and argv[1] == '--worker':
        worker(argv[2])
    config = load_config()
    if '--check' in argv:
        print(json.dumps({'ok': True, 'version': config['version']}))
        return 0
    runtime = Path(os.environ.get('CAPSTONE_IVI_RUN', '/run/capstone-ivi'))
    flag, status, nav_file = runtime / 'map_active', runtime / 'status.json', runtime / 'nav.json'
    stopping = {'now': False}
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.update(now=True))
    enabled = config['map_enabled']          # V2 ships without a map: the flag does nothing there
    route = load_route() if enabled else None
    bus = open_can(os.environ.get('CAPSTONE_IVI_CAN', 'can0')) if enabled else None
    workers, wanted, last, last_loop = [], worker_count(config), 0.0, time.monotonic()
    speed_kmh, distance = 0.0, 0.0
    try:
        while not stopping['now']:
            active = enabled and flag.exists()
            workers = [w for w in workers if w.poll() is None]
            if active and len(workers) < wanted:
                workers += [subprocess.Popen([sys.executable, __file__, '--worker', config['map_priority']])
                            for _ in range(wanted - len(workers))]
            elif not active and workers:
                for w in workers:
                    w.kill()
                for w in workers:
                    w.wait()
                workers = []
            if bus is not None:
                while select.select([bus], [], [], 0)[0]:
                    try:
                        identifier, size, data = CAN_FRAME.unpack(bus.recv(CAN_FRAME.size))
                    except (BlockingIOError, struct.error):
                        break
                    if identifier == 0x200 and size == 8:
                        speed_kmh = int.from_bytes(data[0:2], 'big') / 10.0
            now = time.monotonic()
            distance += speed_kmh / 3.6 * (now - last_loop)
            last_loop = now
            if route is not None:
                x, y, heading = position(route, distance)
                atomic_json(nav_file, {'x': round(x, 1), 'y': round(y, 1), 'heading': round(heading, 1),
                                       'speed': speed_kmh, 'distance': round(distance, 1), 'updated': time.time()})
            if time.monotonic() - last >= 1:
                last = time.monotonic()
                atomic_json(status, {'version': config['version'], 'map_active': active, 'workers': len(workers),
                                     'priority': config['map_priority'], 'pid': os.getpid(), 'updated': time.time(),
                                     'map_enabled': enabled, 'can': bus is not None, 'route': route is not None})
            time.sleep(0.2)
    finally:
        for w in workers:
            w.kill()
        for w in workers:
            w.wait()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}), file=sys.stderr)
        sys.exit(2)
