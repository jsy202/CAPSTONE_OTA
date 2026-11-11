"""Sequential opt-in physical bench orchestration; all results come from real hosts."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from capstone_ota.bundle import pack
from dashboard.qt_cluster.build import payload
from .evidence import Captures

ROOT = Path(__file__).resolve().parents[1]


class Peer:
    def __init__(self, arguments):
        if (not isinstance(arguments, list) or not arguments or arguments[0] not in {'ssh', '/usr/bin/ssh'}
                or any(not isinstance(a, str) or not a or '\x00' in a for a in arguments)):
            raise ValueError('peer must be an explicit trusted ssh argument list')
        self.arguments = arguments

    def run(self, command, *, data=None, check=True, timeout=180, binary=False):
        p = subprocess.run([*self.arguments, command], input=data, capture_output=True,
                           text=not binary, timeout=timeout)
        if check and p.returncode:
            raise RuntimeError(f'remote exit {p.returncode}: {str(p.stderr)[-2500:]} {str(p.stdout)[-1500:]}')
        return p


class PhysicalBench:
    def __init__(self, config, output):
        required = {'central_ssh', 'cluster_ssh', 'runtime', 'laptop_ip', 'arm_binary'}
        if not isinstance(config, dict) or not required <= config.keys():
            raise ValueError('hardware config missing required fields')
        self.central, self.cluster = Peer(config['central_ssh']), Peer(config['cluster_ssh'])
        if ('verifier_ssh' in config) != ('verifier_peer' in config):
            raise ValueError('third-board selection requires verifier_ssh and verifier_peer together')
        self.verifier = Peer(config['verifier_ssh']) if 'verifier_ssh' in config else None
        self.gates = {}
        self.runtime = config['runtime']
        if not isinstance(self.runtime, str) or not self.runtime.startswith('/') or '\x00' in self.runtime:
            raise ValueError('runtime must be an absolute installed bench path')
        self.python = self.runtime + '/venv/bin/python'
        self.binary = Path(config['arm_binary']).resolve()
        header = self.binary.read_bytes()[:20]
        if len(header) != 20 or header[:5] != b'\x7fELF\x02' or int.from_bytes(header[18:20], 'little') != 183:
            raise ValueError('hardware tests require a native aarch64 Qt executable')
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.run_id = 'run-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
        self.base = '/opt/capstone-cluster/runs/' + self.run_id
        self.public = '/opt/capstone-cluster/public/' + self.run_id + '.pub'
        self.fixture = '/opt/capstone-cluster/releases/' + self.run_id + '/v1'
        self.remote_evidence = '/opt/capstone-cluster/evidence/' + self.run_id
        self.uid_log = '/var/lib/capstone-app/init-uid-' + self.run_id
        self.old_marker = '/var/lib/capstone-app/old-app-' + self.run_id
        self.config, self.records, self.last_good = config, {}, None
        self.server = self.thread = self.secrets = None
        self.capture_units = []
        self.captures = None
        self.ota_count = 0

    def root(self, code):
        return self.cluster.run('sudo ' + shlex.join([self.python, '-']), data=code)

    def put(self, peer, path, data, root=False, mode='600'):
        script = 'set -eu\numask 077\nmkdir -p ' + shlex.quote(str(Path(path).parent))
        script += '\ncat > ' + shlex.quote(path) + '\nchmod ' + mode + ' ' + shlex.quote(path)
        command = ('sudo ' if root else '') + 'sh -c ' + shlex.quote(script)
        peer.run(command, data=data, binary=True)

    def prepare(self):
        # Nothing silently skips after an explicit hardware selection.
        self.central.run('hostname\nip -j -details -statistics link show can0\ncommand -v cansend\ncommand -v tshark')
        self.cluster.run('hostname\nip -j -details -statistics link show can0\nsystemctl is-active capstone-cluster-display.service')
        if self.verifier:
            self.verifier.run('hostname\nip -j -details -statistics link show can0\ncommand -v candump')
        self.record('CAN-LINK-BEFORE', self.links())
        self.cluster.run('sudo systemctl stop capstone-cluster.service', check=False)
        self.central.run('sudo systemctl stop capstone-demo-source.service', check=False)
        self.put(self.cluster, '/opt/capstone-cluster/verify-service.py',
                 (ROOT / 'bench/verify_service.py').read_bytes(), root=True, mode='644')
        self.secrets = tempfile.TemporaryDirectory(prefix='capstone-hardware-private-')
        private = Ed25519PrivateKey.generate()
        key = Path(self.secrets.name) / 'signing.key'
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        key.chmod(0o600)
        pub = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.put(self.cluster, self.public, pub, root=True, mode='644')
        self.put(self.central, self.runtime + '/' + self.run_id + '.pub', pub, mode='644')
        for label, version, svn in [('v1', '1.0.0', 1), ('v2', '2.0.0', 2), ('v3', '3.0.0', 2)]:
            slot = payload(self.binary, Path(self.secrets.name) / label)
            (slot / 'bench-release.txt').write_text(label)
            app = slot / 'bin/app'
            text = app.read_text().replace('set -eu', 'set -eu\nif [ "${1:-}" = "--check" ]; then id -u >> '
                                          + shlex.quote(self.uid_log) + '; fi', 1)
            if label == 'v1':
                text = text.replace('exec "$task_root/libexec/dashboard"',
                                    'touch ' + shlex.quote(self.old_marker) + '\nexec "$task_root/libexec/dashboard"')
            app.write_text(text)
            pack(slot, self.output / 'releases' / label, version, svn, key)
        for name in ('release.json', 'release.sig', 'app.tar.gz'):
            self.put(self.cluster, self.fixture + '/' + name,
                     (self.output / 'releases/v1' / name).read_bytes(), root=True)
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass
        self.server = ThreadingHTTPServer((self.config['laptop_ip'], 0),
                                         partial(Handler, directory=str(self.output / 'releases')))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://' + self.config['laptop_ip'] + ':' + str(self.server.server_port)
        self.record('ENVIRONMENT', {'run_id': self.run_id, 'commit': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'working_tree_status': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
            'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for folder in ('capstone_ota', 'bench', 'tests/hardware', 'deploy/can-bench')
                              for p in sorted((ROOT / folder).rglob('*'))
                              if p.is_file() and '__pycache__' not in p.parts},
            'arm_binary_sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            'scope': ('physical three-board CAN; Pi3 direct witness, Cluster model/QML via SSH; no optical oracle'
                      if self.verifier else 'physical two-board CAN; observer and Qt on Cluster; no independent third CAN board or optical oracle')})
        self.captures = Captures(self.central, self.cluster, self.runtime, self.run_id, self.output,
                                 verifier=self.verifier)
        self.captures.start()

    def links(self):
        peers = [('central', self.central), ('cluster', self.cluster)]
        if self.verifier:
            peers.append(('verifier', self.verifier))
        return {role: {'can': json.loads(peer.run('ip -j -details -statistics link show can0').stdout),
                       'kernel': peer.run('uname -r').stdout.strip()}
                for role, peer in peers}

    def record(self, case, report):
        self.records[case] = report
        path = self.output / (case + '.json')
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        try:
            import allure
            allure.attach.file(str(path), name=case, attachment_type=allure.attachment_type.JSON)
        except ImportError:
            pass
        for section in ('baseline', 'candidate'):
            for i, result in enumerate(report.get(section, {}).get('status', {}).get('verification', [])):
                capture = result.get('capture')
                if not capture:
                    continue
                if not capture.startswith(self.remote_evidence + '/') or not capture.endswith('.png'):
                    raise ValueError('capture path escaped this hardware run')
                data = self.cluster.run('sudo cat ' + shlex.quote(capture), binary=True).stdout
                target = self.output / f'{case}-{section}-{i}.png'
                target.write_bytes(data)
                try:
                    import allure
                    allure.attach.file(str(target), name=target.stem, attachment_type=allure.attachment_type.PNG)
                except ImportError:
                    pass

    def observer_config(self, missing=False):
        observer = {'cluster': {'socket': '/run/capstone-cluster/missing.sock' if missing else
                               '/run/capstone-cluster/model.sock'},
                    'central': {'host': 'capstone-source', 'checkout': self.runtime,
                                'python': self.python, 'channel': 'can0'}, 'observe_channel': 'can0'}
        if self.verifier:
            observer['cluster'].update(host='capstone-cluster-observer', checkout=self.runtime, python=self.python)
        return observer

    def put_observer(self, path, spec):
        self.put(self.verifier or self.cluster, path, json.dumps(spec).encode(), root=not self.verifier)

    def new_device(self, case, source='latest', missing=False, attack=False):
        device = self.base + '/' + case + '/device'
        observer = self.observer_config(missing)
        observer_path = '/opt/capstone-cluster/configs/' + self.run_id + '-' + case + '.json'
        if self.verifier:
            observer_path = self.runtime + '/configs/' + self.run_id + '-' + case + '.json'
        self.put_observer(observer_path, observer)
        checker = [self.python, '/opt/capstone-cluster/verify-service.py', '--config', observer_path,
                   '--capture-dir', self.remote_evidence + '/' + case, '--source', source]
        if self.verifier:
            gate = '/opt/capstone-cluster/configs/' + self.run_id + '-' + case + '-gate.json'
            self.put(self.cluster, gate, json.dumps(dict(self.config['verifier_peer'], peers=observer_path)).encode(), root=True)
            self.gates[observer_path] = gate
            checker += ['--verifier', gate]
        if attack:
            checker += ['--substitute-old', self.fixture, '--device-root', device, '--public-key', self.public]
        config = {'host': 'capstone-target-1793549816', 'checkout': self.runtime,
                  'python': '/opt/capstone-cluster/root-python', 'root': device, 'public_key': self.public,
                  'check_command': checker, 'systemd': True}
        self.root("from pathlib import Path\nimport subprocess\nsubprocess.run(['systemctl','stop','capstone-cluster.service'],check=True)\np=Path("+repr(device)+");p.mkdir(parents=True,mode=0o755)\nPath('/opt/capstone-cluster/selected-device').write_text("+repr(device+'\n')+")\n")
        return device, config, observer_path

    def forward(self, label, config):
        path = self.runtime + '/' + self.run_id + '-target.json'
        self.put(self.central, path, json.dumps(config).encode())
        args = [self.python, '-m', 'capstone_ota', 'forward', self.origin + '/' + label,
                '--public-key', self.runtime + '/' + self.run_id + '.pub', '--target', path,
                '--bind', '10.77.0.1', '--advertise', '10.77.0.1']
        code = 'import os,subprocess\np=subprocess.run(' + repr(args) + ',env=dict(os.environ,PATH='
        code += repr(self.runtime + '/bin:') + '+os.environ["PATH"]))\nraise SystemExit(p.returncode)\n'
        p = self.central.run(shlex.join([self.python, '-']), data=code, check=False)
        self.ota_count += 1
        if p.returncode not in (0, 1, 2):
            raise RuntimeError(p.stderr[-2000:])
        try:
            status = json.loads(p.stdout)
        except ValueError as exc:
            raise ValueError('missing target status: ' + p.stdout[-1200:] + p.stderr[-1200:]) from exc
        return {'exit_code': p.returncode, 'status': status}

    def scenario(self, name):
        if name not in {'compatible', 'attack', 'missing-evidence', 'missing-witness'}:
            raise ValueError('unknown hardware scenario')
        case = name + '-' + uuid.uuid4().hex[:6]
        device, config, observer = self.new_device(case, source='latest',
                                                   attack=name == 'attack')
        baseline = self.forward('v2', config)
        if baseline['status']['result'] != 'committed':
            self.record('BASELINE-FAIL-' + case, {'baseline': baseline})
            raise AssertionError(baseline)
        if name in {'missing-evidence', 'missing-witness'}:
            spec = self.observer_config(missing=name == 'missing-evidence')
            if name == 'missing-witness':
                spec['observe_channel'] = 'absent-can'
            self.put_observer(observer, spec)
        candidate = self.forward('v3', config)
        progress = json.loads(self.cluster.run('sudo cat ' + shlex.quote(device + '/progress.json')).stdout)
        result = {'device': device, 'baseline': baseline, 'candidate': candidate, 'progress': progress}
        if name == 'attack':
            result['old_app_executed'] = self.cluster.run('test -e ' + shlex.quote(self.old_marker), check=False).returncode == 0
        self.record('SCENARIO-' + case, result)
        return result

    def debug_port_rollback(self):
        """A real, unauthenticated debug port (capstone_ota.debug_port) swaps the
        recovery slot WHILE V3 is on trial, through the real network service this
        time rather than the trusted in-process substitute_old fixture scenario('attack')
        uses. Device._verify_record's pinned tree-hash check must block the rollback
        from activating the swapped slot.

        The V3-trial failure here is forced deterministically by the check_command
        itself (same mechanism as the fake-Pi3 verifier in the software test,
        tests/test_debug_port_rollback_scenario.py) rather than by a genuine
        Probation/monitor.detect p99 CPU-contention verdict: that real-time CAN
        monitor is not deployed as a running service on the Verifier board in this
        bench, so it cannot yet gate a real rollback here. bench/load_phases.py still
        runs alongside for a real (if not yet verdict-gating) CPU load; wiring
        Probation into PhysicalBench as the actual trigger is a separate follow-up
        (see task_plan.md)."""
        case = 'debugport-' + uuid.uuid4().hex[:6]
        device, config, observer = self.new_device(case, source='latest', attack=False)
        baseline = self.forward('v2', config)
        if baseline['status']['result'] != 'committed':
            self.record('BASELINE-FAIL-' + case, {'baseline': baseline})
            raise AssertionError(baseline)

        port_unit, load_unit, debug_port = 'capstone-debugport-' + self.run_id, 'capstone-cpuload-' + self.run_id, 7312
        self.cluster.run(shlex.join(['sudo', 'systemd-run', '--quiet', '--collect', '--unit', port_unit,
                         self.python, '-m', 'capstone_ota.debug_port', '--device-root', device,
                         '--bind', '127.0.0.1', '--port', str(debug_port)]))
        self.cluster.run(shlex.join(['sudo', 'systemd-run', '--quiet', '--collect', '--unit', load_unit,
                         '--property=RuntimeMaxSec=180', self.python, self.runtime + '/bench/load_phases.py',
                         self.remote_evidence + '/' + case + '-load.json']))
        # self.fixture's archive is root:600 (prepare() uploads it that way for the
        # trusted in-process attack fixture); this check_command instead runs as
        # the ordinary user the SSH relay logs in as, so stage an otauser-readable
        # copy for it to push -- the "attacker" in this scenario is assumed to
        # already have that V1 archive, not to need root on the victim board.
        planted = self.runtime + '/' + case + '-planted.tar.gz'
        self.cluster.run('sudo cp ' + shlex.quote(self.fixture + '/app.tar.gz') + ' ' + shlex.quote(planted)
                         + ' && sudo chmod 644 ' + shlex.quote(planted))
        # This check_command replaces the default functional checker for the V3
        # trial only (V2 above already committed with the default checker): it
        # fires the debug-port attack against the recovery slot right when the
        # trial is evaluated, then reports a confirmed failure, forcing a rollback.
        verifier_script = (
            "import json\n"
            "from capstone_ota.debug_port import push\n"
            "push('127.0.0.1', " + str(debug_port) + ", 'A', " + repr(planted) + ")\n"
            "print(json.dumps({'passed': False, 'error': 'debug-port-attack-forced-failure'}))\n"
        )
        config['check_command'] = [self.python, '-c', verifier_script]
        try:
            candidate = self.forward('v3', config)
        finally:
            self.cluster.run(shlex.join(['sudo', 'systemctl', 'stop', port_unit]), check=False)
            self.cluster.run(shlex.join(['sudo', 'systemctl', 'stop', load_unit]), check=False)

        progress = json.loads(self.cluster.run('sudo cat ' + shlex.quote(device + '/progress.json')).stdout)
        outcome = {'device': device, 'baseline': baseline, 'candidate': candidate, 'progress': progress,
                  'old_app_executed': self.cluster.run('test -e ' + shlex.quote(self.old_marker), check=False).returncode == 0}
        self.record('SCENARIO-' + case, outcome)
        return outcome

    def two_way(self):
        directions = []
        peers = [(self.central, self.cluster, '123'), (self.cluster, self.central, '124')]
        if self.verifier:
            peers += [(self.central, self.verifier, '125'), (self.verifier, self.cluster, '126')]
        for sender, receiver, identifier in peers:
            receive = subprocess.Popen([*receiver.arguments, 'timeout 10 candump -L -n 3 can0,' + identifier + ':7FF'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(12):
                    ready = receiver.run('cat /proc/net/can/rcvlist_sff /proc/net/can/rcvlist_fil').stdout.lower()
                    if re.search(r'can0\s+0*' + identifier + r'\s', ready):
                        break
                    time.sleep(.1)
                else:
                    raise ValueError('CAN receiver did not become ready')
                values = ['0011223344556677', '1020304050607080', 'AABBCCDDEEFF1122']
                sender.run('set -e\n' + '\n'.join('cansend can0 ' + identifier + '#' + v + '\nsleep .05' for v in values))
                text, error = receive.communicate(timeout=12)
                actual = [v.upper() for v in re.findall(identifier + r'#([A-Fa-f0-9]+)', text)]
                directions.append({'identifier': identifier, 'expected': values, 'actual': actual,
                                   'passed': receive.returncode == 0 and actual == values,
                                   'candump': text, 'stderr': error})
            finally:
                if receive.poll() is None:
                    receive.terminate()
                    receive.wait(timeout=3)
        return {'passed': all(x['passed'] for x in directions), 'directions': directions}

    def select_good(self):
        if self.last_good is None:
            device, config, observer = self.new_device('normal-' + uuid.uuid4().hex[:6])
            baseline = self.forward('v2', config)
            if baseline['status']['result'] != 'committed':
                raise AssertionError(baseline)
            self.last_good = (device, observer)
        device, observer = self.last_good
        self.root("from pathlib import Path\nimport subprocess\nsubprocess.run(['systemctl','stop','capstone-cluster.service'],check=True)\nPath('/opt/capstone-cluster/selected-device').write_text("+repr(device+'\n')+")\nsubprocess.run(['systemctl','start','capstone-cluster.service'],check=True)\n")
        return device, observer

    def signals(self):
        device, observer = self.select_good()
        code = "from pathlib import Path\nimport json,time\nfrom capstone_ota.qt_verify import remote_dashboard,deliver,snapshot\n"
        code += 'with remote_dashboard(json.loads(Path(' + repr(observer) + ').read_text()),' + repr(device + '/slots/A') + ') as t:\n'
        code += ''' a=deliver(t,600,255,rpm=2200,gear=3,warnings=1)
 assert a['model_speed']==a['qml_speed']==60 and a['rpm']==2200 and a['gear']==3 and a['warning'] is True
 b=deliver(t,2000,0,corrupt=True)
 assert b['qml_speed']==60 and b['source_counter']==255
 c=deliver(t,610,0,rpm=2300,gear=4,warnings=0)
 assert c['qml_speed']==61 and c['rpm']==2300 and c['gear']==4 and c['warning'] is False
 d=deliver(t,350,254)
 assert d['qml_speed']==61 and d['source_counter']==0
 time.sleep(.65)
 assert snapshot(t)['valid'] is False
 e=deliver(t,620,1)
 assert e['valid'] is True and e['qml_speed']==62
 print(json.dumps({'passed':True,'checks':['speed/model/QML','RPM','gear','warning','CRC','255-to-0 wrap','stale replay','timeout','fresh recovery'],'final':e}))
'''
        result = (self.verifier.run(shlex.join([self.python, '-']), data=code)
                  if self.verifier else self.root(code))
        return json.loads(result.stdout)

    def load(self):
        device, observer = self.select_good()
        # Keep bounded traffic active across the entire verification interval.
        unit = 'capstone-load-' + self.run_id
        self.central.run(shlex.join(['sudo', 'systemd-run', '--quiet', '--collect', '--unit', unit,
                         '--property=RuntimeMaxSec=30', 'cangen', 'can0', '-I', '321', '-L', '8',
                         '-D', 'i', '-g', '5', '-n', '6000']))
        measure = subprocess.Popen([*self.central.arguments, 'timeout 4 canbusload can0@500000 -r'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        active = ['sudo', 'systemctl', 'is-active', '--quiet', unit]
        try:
            self.central.run(shlex.join(active))
            started = time.monotonic()
            checker = ['sudo', self.python, '/opt/capstone-cluster/verify-service.py',
                                      '--config', observer, '--capture-dir', self.remote_evidence + '/load',
                                      device + '/slots/A']
            if self.verifier:
                checker += ['--verifier', self.gates[observer]]
            result = self.cluster.run(shlex.join(checker), check=False)
            elapsed = time.monotonic() - started
            self.central.run(shlex.join(active))  # Failure/expiry cannot be reported as a loaded PASS.
            text, error = measure.communicate(timeout=10)
            return {'verification': json.loads(result.stdout), 'load_active_before_and_after': True,
                    'verification_elapsed_s': elapsed,
                    'load_tool': re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text), 'load_stderr': error,
                    'profile': {'unrelated_id': '0x321', 'requested_gap_ms': 5,
                                'max_frames': 6000, 'max_seconds': 30}}
        finally:
            self.central.run(shlex.join(['sudo', 'systemctl', 'stop', unit]), check=False)
            if measure.poll() is None:
                measure.terminate()
                measure.communicate(timeout=5)

    def initialization_identity(self):
        if self.last_good is None:
            self.select_good()
        data = self.root('from pathlib import Path\nimport json,pwd\nprint(json.dumps({"app_uid":pwd.getpwnam("capstone-app").pw_uid,"uids":[int(x) for x in Path('+repr(self.uid_log)+').read_text().split()]}))\n')
        return json.loads(data.stdout)

    def packets(self):
        if not self.ota_count:
            self.scenario('compatible')
        report = self.captures.packets()
        report['expected_file_requests'] = self.ota_count * 3
        report['ota_count'] = self.ota_count
        return report

    def close(self):
        errors = []
        try:
            self.record('CAN-LINK-AFTER', self.links())
        except Exception as exc:
            errors.append(str(exc))
        try:
            if self.captures:
                errors.extend(self.captures.close())
        except Exception as exc:
            errors.append(str(exc))
        try:
            # Operator cleanup, deliberately separate from failed OTA journals.
            # Never describe this as automatic recovery of the attacked device.
            if self.last_good:
                device, observer = self.select_good()
                self.put(self.central, self.runtime + '/demo_source.py',
                         (ROOT / 'bench/demo_source.py').read_bytes(), mode='644')
                command = ['sudo', 'systemd-run', '--quiet', '--collect', '--unit=capstone-demo-source',
                           '--property=User=otauser', '--property=Restart=on-failure',
                           self.python, self.runtime + '/demo_source.py']
                self.central.run(shlex.join(command))
                time.sleep(.5)
                code = "import json\nfrom capstone_ota.qt_verify import socket_request\nprint(json.dumps(socket_request('/run/capstone-cluster/model.sock',{'op':'snapshot','nonce':'bench-cleanup'})))\n"
                result = self.root(code)
                state = json.loads(result.stdout)
                if not state.get('valid') or state.get('qml_speed') != 65:
                    raise ValueError('normal V2 demo cleanup did not produce fresh 65 km/h')
                self.record('BENCH-CLEANUP', {'operator_cleanup': True, 'device': device,
                                            'snapshot': state, 'automatic_attack_recovery': False})
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if self.server:
                self.server.shutdown(); self.server.server_close(); self.thread.join()
            if self.secrets:
                self.secrets.cleanup()
        if errors:
            self.record('BENCH-CLEANUP-ERROR', {'errors': errors})
            raise RuntimeError('bench cleanup/evidence failed: ' + '; '.join(errors))
