"""Independent input oracle and observer for the real Qt Cluster model/QML binding."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shlex
import selectors
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

from .process import exchange
from .qt_source import frame


def socket_request(path, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1)
        client.connect(str(path))
        client.sendall(json.dumps(request).encode() + b'\n')
        output = bytearray()
        while b'\n' not in output:
            part = client.recv(4096)
            if not part or len(output) + len(part) > 65536:
                raise ValueError('missing or excessive Qt snapshot')
            output.extend(part)
    return json.loads(output)


def snapshot(target, op='snapshot'):
    nonce = secrets.token_hex(8)
    request = {'op': op, 'nonce': nonce}
    if target.get('observer_command'):
        rows, _pid = exchange(target['observer_command'], [request], timeout=3)
        if len(rows) != 1:
            raise ValueError('missing remote Qt observation')
        report = rows[0]
    else:
        report = socket_request(target['socket'], request)
    if (not isinstance(report, dict) or report.get('nonce') != nonce
            or ('pid' in target and report.get('pid') != target['pid'])
            or ('executable' in target and Path(report.get('executable', '')).resolve() != Path(target['executable']).resolve())):
        raise ValueError('Qt snapshot identity/challenge mismatch')
    return report


@contextmanager
def rendering_environment(capture, log):
    if os.environ.get('CAPSTONE_QT_LIVE') == '1':
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            raise ValueError('live Qt presentation requires a display')
        yield dict(os.environ, QT_QUICK_BACKEND='software')
        return
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    display = None
    try:
        if capture:
            executable = shutil.which('Xvfb')
            if not executable:
                raise ValueError('Xvfb is required for hidden rendered screenshots')
            display = subprocess.Popen([executable, '-displayfd', '1', '-screen', '0', '1280x480x24',
                                        '-nolisten', 'tcp'], stdout=subprocess.PIPE, stderr=log,
                                       start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(display.stdout, selectors.EVENT_READ)
                if not selector.select(3):
                    raise ValueError('virtual screen startup timed out')
                number = os.read(display.stdout.fileno(), 32).strip()
            if not number.isdigit():
                raise ValueError('virtual screen failed to initialize')
            environment.update(DISPLAY=':' + number.decode(), QT_QPA_PLATFORM='xcb')
        yield environment
    finally:
        if display:
            display.terminate()
            try:
                display.wait(timeout=2)
            except subprocess.TimeoutExpired:
                display.kill(); display.wait()
            display.stdout.close()


@contextmanager
def running_dashboard(payload, capture=None):
    payload = Path(payload).resolve()
    with tempfile.TemporaryDirectory(prefix='capstone-qt-') as temporary:
        control = Path(temporary) / 'model.sock'
        log = Path(temporary) / 'qt.log'
        command = [str(payload / 'bin/app'), '--socket', str(control), '--udp-port', '0']
        if capture:
            command += ['--capture', str(Path(capture).absolute())]
        with log.open('w') as stream, rendering_environment(capture, stream) as environment:
            process = subprocess.Popen(command, cwd=temporary, env=environment, stdout=stream, stderr=stream,
                                       start_new_session=True)
            target = {'socket': str(control), 'pid': process.pid, 'executable': str(payload / 'libexec/dashboard')}
            try:
                deadline = time.monotonic() + 5
                while True:
                    if process.poll() is not None:
                        raise ValueError('Qt startup failed: ' + log.read_text()[-2000:])
                    try:
                        report = snapshot(target)
                        if report.get('ui_ready') is not True:
                            raise ValueError('Qt QML still loading')
                        target['port'] = report['udp_port']
                        break
                    except (OSError, ValueError):
                        if time.monotonic() > deadline:
                            raise ValueError('Qt startup deadline: ' + log.read_text()[-2000:])
                        time.sleep(0.02)
                yield target
            finally:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def deliver(target, speed, counter, rpm=1500, gear=3, warnings=0, corrupt=False):
    before = snapshot(target)['generation']
    message = frame(speed, counter, rpm, gear, warnings)
    if corrupt:
        raw = bytearray.fromhex(message['data']); raw[-1] ^= 1
        message['data'] = raw.hex()
    messages = [message]
    injection = ['--frame', json.dumps(message)]
    witness = target.get('witness')
    if witness:
        witness.setblocking(False)
        for _ in range(100):
            try:
                witness.recv(16)
            except BlockingIOError:
                break
        else:
            raise ValueError('uncontrolled CAN traffic before trial')
        witness.settimeout(1)
    if target.get('source_peer'):
        command = remote_command(target['source_peer'],
                                 [*target['source_arguments'], *injection],
                                 module='capstone_ota.qt_source')
    else:
        prefix = target.get('source_command') or [sys.executable, '-m', 'capstone_ota.qt_source',
                                                 '--udp-port', str(target['port'])]
        command = [*prefix, *injection]
    rows, _pid = exchange(command, [], timeout=3)
    if rows != [{'sent': len(messages)}]:
        raise ValueError('Central source did not send the requested frame count')
    if witness:
        for expected in messages:
            raw = witness.recv(16)
            identifier, size, data = struct.unpack('=IB3x8s', raw)
            if identifier != 0x200 or size != 8 or data.hex() != expected['data']:
                raise ValueError('direct CAN observation differs from requested input')
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        report = snapshot(target)
        if report['generation'] >= before + len(messages):
            if report['generation'] != before + len(messages) or report['received_counter'] != counter:
                raise ValueError('unexpected extra or mismatched frame')
            return report
        time.sleep(0.005)
    raise ValueError('Cluster did not process input frame')


def verify(target, source='latest', cycles=3):
    if source != 'latest':
        raise ValueError('unsupported source profile; replay display scenario was removed')
    evidence = []
    passed = True
    started = time.monotonic()
    def sample(speed, counter, expected):
        nonlocal passed
        actual = deliver(target, speed, counter)
        ok = (actual['model_speed'] == expected and actual['qml_speed'] == expected
              and actual['valid'] is True)
        passed &= ok
        evidence.append({'input_speed': speed / 10, 'counter': counter, 'expected': expected,
                         'model': actual['model_speed'], 'qml': actual['qml_speed'], 'valid': actual['valid'],
                         'elapsed_s': round(time.monotonic() - started, 6), 'passed': ok})
        if not ok and sum(not row['passed'] for row in evidence) == 1:
            snapshot(target, 'capture')
    # Fixed literal oracle. V3 cannot know or choose these expected values.
    sample(400, 240, 40.0); sample(450, 241, 45.0); sample(600, 242, 60.0)
    unavailable = True
    counter = 243
    for _ in range(cycles):
        time.sleep(0.65)  # Fault injection: link silence, not a fabricated invalid measurement.
        unavailable &= snapshot(target)['valid'] is False
        passed &= unavailable
        sample(620, counter % 256, 62.0)
        counter += 1
        sample(650, counter % 256, 65.0)
        counter += 1
    if passed:
        snapshot(target, 'capture')
    display_mismatch = any(row['model'] != row['expected'] or row['qml'] != row['expected'] for row in evidence)
    error = None if passed else 'SIGNAL_MODEL_MISMATCH' if display_mismatch else 'INPUT_FRESHNESS_FAILURE'
    return {'passed': bool(passed), 'error': error,
            'transport': target.get('transport', 'loopback UDP emulation'), 'source_profile': source, 'cycles': cycles,
            'timeout_detected': unavailable, 'pid': target.get('pid'), 'observed': evidence}


def check_slot(slot, source='latest', capture_dir=None):
    capture = None
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        capture = Path(capture_dir) / f'{Path(slot).name}-{time.monotonic_ns()}.png'
    with running_dashboard(slot, capture=capture) as target:
        report = verify(target, source)
    if capture and capture.exists():
        report['capture'] = str(capture)
    return report


def remote_command(peer, arguments, module='capstone_ota.qt_verify'):
    from .cluster import check_command
    import re
    if (not isinstance(peer, dict) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', peer.get('host', ''))
            or any(not isinstance(peer.get(k), str) or not peer[k].startswith('/') for k in ('checkout', 'python'))):
        raise ValueError('invalid SSH peer configuration')
    command = [peer['python'], '-m', module, *arguments]
    check_command(command)
    remote = 'cd ' + shlex.quote(peer['checkout']) + ' && ' + shlex.join(command)
    return ['ssh', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oConnectTimeout=5', peer['host'], remote]


@contextmanager
def remote_dashboard(config, slot):
    cluster, central = config['cluster'], config['central']
    target = {'executable': str(Path(slot) / 'libexec/dashboard')}
    local = 'host' not in cluster
    if local:
        path = cluster.get('socket')
        if set(cluster) != {'socket'} or not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
            raise ValueError('local observer requires an absolute socket path')
        target['socket'] = path
    else:
        target['observer_command'] = remote_command(cluster, ['snapshot', '--socket', cluster['socket']])
    deadline = time.monotonic() + 5
    while True:
        try:
            initial = snapshot(target)
            if initial.get('ui_ready') is True:
                break
        except OSError:
            pass  # The separate service may still be creating its model socket.
        if time.monotonic() >= deadline:
            raise ValueError('selected Qt application is not ready')
        time.sleep(.05)
    target['pid'] = initial['pid']
    args = ['--udp-port', str(initial['udp_port'])] if config.get('emulated_udp') else ['--can-channel', central['channel']]
    target.update(source_peer=central, source_arguments=args)
    witness = None
    try:
        if not config.get('emulated_udp'):
            witness = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
            witness.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack('=II', 0x200, 0xC00007FF))
            witness.bind((config['observe_channel'],))
            target.update(witness=witness, transport='SocketCAN observed on Cluster; local model observer' if local
                          else 'SocketCAN directly observed by verifier')
        else:
            target['transport'] = 'loopback UDP emulation with remote control commands'
        yield target
    finally:
        if witness:
            witness.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'snapshot':
        parser = argparse.ArgumentParser(description='Cluster-local read-only model observer')
        parser.add_argument('--socket', required=True)
        args = parser.parse_args(sys.argv[2:])
        try:
            raw = sys.stdin.buffer.read(4097)
            if len(raw) > 4096:
                raise ValueError('observer request too large')
            request = json.loads(raw)
            if (not isinstance(request, dict) or set(request) != {'op', 'nonce'}
                    or request['op'] not in {'snapshot', 'capture'} or not isinstance(request['nonce'], str)):
                raise ValueError('invalid observer request')
            print(json.dumps(socket_request(args.socket, request)))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({'passed': False, 'error': str(exc)}))
            return 1
    if len(sys.argv) > 1 and sys.argv[1] == 'remote-gate':
        parser = argparse.ArgumentParser(description='Cluster calls its trusted third-Pi verifier')
        parser.add_argument('--config', type=Path, required=True)
        parser.add_argument('slot')
        args = parser.parse_args(sys.argv[2:])
        try:
            config = json.loads(args.config.read_text())
            command = remote_command(config, ['--remote', config['peers'], args.slot])
            rows, _pid = exchange(command, [], timeout=50, allow_negative_verdict=True)
            if len(rows) != 1 or not isinstance(rows[0], dict) or type(rows[0].get('passed')) is not bool:
                raise ValueError('invalid third-Pi verdict')
            print(json.dumps(rows[0]))
            return 0 if rows[0]['passed'] else 1
        except (OSError, ValueError, KeyError) as exc:
            print(json.dumps({'passed': False, 'error': str(exc)}))
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['latest'], default='latest')
    parser.add_argument('--capture-dir', type=Path)
    parser.add_argument('--remote', type=Path, help='Pi3 peer configuration; observe existing Qt service')
    parser.add_argument('slot', type=Path)
    args = parser.parse_args()
    try:
        if args.remote:
            with remote_dashboard(json.loads(args.remote.read_text()), args.slot) as target:
                report = verify(target, args.source)
        else:
            report = check_slot(args.slot, args.source, args.capture_dir)
        print(json.dumps(report))
        return 0 if report['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
