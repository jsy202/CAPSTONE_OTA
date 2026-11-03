"""Bounded real can-utils/tcpdump capture and tshark analysis for one bench run."""
from pathlib import Path
import shlex


def analyze_http(rows):
    fields = [line.split('\t') for line in rows.splitlines()]
    requests = {r[0]: r[4].rsplit('/', 1)[-1] for r in fields if len(r) >= 7 and r[3] == 'GET'}
    successful = {'release.json': 0, 'release.sig': 0, 'app.tar.gz': 0}
    for r in fields:
        if len(r) >= 7 and r[5] == '200' and requests.get(r[6]) in successful:
            successful[requests[r[6]]] += 1
    return {'http_requests': len(requests),
            'http_200_responses': sum(len(r) >= 7 and r[5] == '200' for r in fields),
            'successful_files': successful}


class Captures:
    def __init__(self, central, cluster, runtime, run_id, output):
        self.central, self.cluster = central, cluster
        self.remote = runtime + '/captures/' + run_id
        self.run_id, self.output = run_id, Path(output)
        self.units, self.pcap_stopped = [], False

    def start(self):
        for role, peer in [('central', self.central), ('cluster', self.cluster)]:
            peer.run('sudo mkdir -p ' + shlex.quote(self.remote))
            self.launch(peer, 'can-' + role, ['/usr/bin/candump', '-L', 'can0'], self.remote + '/' + role + '-can.log')
        self.launch(self.central, 'ethernet', ['/usr/bin/tcpdump', '-Z', 'root', '-U', '-n', '-i', 'eth0',
                    '-s', '0', '-w', self.remote + '/ethernet.pcap', 'tcp and host 10.77.0.2'])

    def launch(self, peer, suffix, command, output=None):
        unit = 'capstone-capture-' + self.run_id + '-' + suffix
        args = ['sudo', 'systemd-run', '--quiet', '--collect', '--unit', unit,
                '--property=RuntimeMaxSec=900']
        if output:
            args.append('--property=StandardOutput=append:' + output)
        peer.run(shlex.join([*args, *command]))
        peer.run(shlex.join(['sudo', 'systemctl', 'is-active', '--quiet', unit]))
        self.units.append((peer, unit, suffix))

    def stop_unit(self, peer, unit):
        peer.run(shlex.join(['sudo', 'systemctl', 'kill', '--signal=SIGINT', unit]), check=False)
        peer.run(shlex.join(['sudo', 'systemctl', 'stop', unit]), check=False)

    def packets(self):
        if not self.pcap_stopped:
            for peer, unit, suffix in self.units:
                if suffix == 'ethernet':
                    self.stop_unit(peer, unit)
            self.pcap_stopped = True
        path = self.remote + '/ethernet.pcap'
        # Decode actual observed HTTP destination ports, not a guessed fixed port.
        base = ['sudo', 'tshark', '-r', path]
        ports = self.central.run(shlex.join([*base, '-Y', 'tcp contains "GET /"', '-T', 'fields', '-e', 'tcp.dstport'])).stdout
        decode = []
        for port in sorted(set(ports.split())):
            if not port.isdigit():
                raise ValueError('invalid captured TCP port')
            decode += ['-d', 'tcp.port==' + port + ',http']
        rows = self.central.run(shlex.join([*base, *decode, '-Y', 'http.request || http.response', '-T', 'fields',
                    '-e', 'frame.number', '-e', 'ip.src', '-e', 'ip.dst', '-e', 'http.request.method',
                    '-e', 'http.request.uri', '-e', 'http.response.code', '-e', 'http.request_in'])).stdout
        packet = self.central.run('sudo cat ' + shlex.quote(path), binary=True).stdout
        (self.output / 'ethernet.pcap').write_bytes(packet)
        (self.output / 'http-analysis.tsv').write_text('frame\tsource\tdestination\tmethod\turi\tstatus\trequest_frame\n' + rows)
        return {**analyze_http(rows),
                'analysis': rows, 'pcap_bytes': len(packet),
                'tool_versions': self.central.run('dpkg-query -W can-utils tcpdump tshark').stdout}

    def close(self):
        errors = []
        for peer, unit, _suffix in reversed(self.units):
            try:
                self.stop_unit(peer, unit)
            except Exception as exc:
                errors.append(str(exc))
        for role, peer in [('central', self.central), ('cluster', self.cluster)]:
            try:
                data = peer.run('sudo cat ' + shlex.quote(self.remote + '/' + role + '-can.log'), binary=True).stdout
                (self.output / (role + '-can.log')).write_bytes(data)
            except Exception as exc:
                errors.append(str(exc))
        # Preserve raw Ethernet evidence even when an earlier failure prevented NET-001.
        try:
            data = self.central.run('sudo cat ' + shlex.quote(self.remote + '/ethernet.pcap'), binary=True).stdout
            (self.output / 'ethernet.pcap').write_bytes(data)
        except Exception as exc:
            errors.append(str(exc))
        return errors
