"""Mock IVI payload: config validation, check mode, and map-driven worker lifecycle."""
import json
import os
import subprocess
import sys
import time

import pytest

from dashboard.ivi.build import CONFIGS, payload


def _stop_app(app):
    """End the app so that it also ends its busy workers (SIGTERM). A bare kill() orphans them, and they then burn a core
    until reboot: dozens of leftover workers made later runs and the whole machine slow and the timing tests flaky."""
    if app.poll() is None:
        app.terminate()
        try:
            app.wait(timeout=5)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait()


def run(slot, *args, env=None, timeout=10):
    return subprocess.run([str(slot / 'bin/app'), *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, 'CAPSTONE_IVI_CAN': '', **(env or {})})


def test_payload_layout_and_versions(tmp_path):
    mapfile = tmp_path / 'map.json'
    mapfile.write_text('{"route": {"p": [0, 0, 1, 1]}}')
    for version in ('V1', 'V2', 'V3'):
        slot = payload(tmp_path / version, version, map_file=mapfile)
        assert (slot / 'bin/app').is_file() and os.access(slot / 'bin/app', os.X_OK)
        assert json.loads((slot / 'ivi.json').read_text()) == CONFIGS[version]
    # V2 is the head unit WITHOUT a map; V3 adds the map and its tile-prefetch workers.
    assert CONFIGS['V2'] == {'version': 'V2', 'map_enabled': False, 'map_workers': 0, 'map_priority': 'normal',
                          'vehicle_data': True}
    assert CONFIGS['V3'] == {'version': 'V3', 'map_enabled': True, 'map_workers': 'cores', 'map_priority': 'rr10',
                          'map_duty': 0.4, 'vehicle_data': True}
    assert not (tmp_path / 'V2/map.json').exists() and (tmp_path / 'V3/map.json').exists()
    # V1 is the oldest release (a radio instead of a map): no map payload, and it is what a rollback attack would run.
    assert CONFIGS['V1'] == {'version': 'V1', 'map_enabled': False, 'map_workers': 0, 'map_priority': 'normal',
                          'vehicle_data': True}
    assert not (tmp_path / 'V1/map.json').exists()
    assert json.loads(run(tmp_path / 'V1', '--check').stdout) == {'ok': True, 'version': 'V1'}


def test_check_passes_for_valid_config_and_fails_for_invalid(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    assert run(slot, '--check').returncode == 0
    for bad in ({'version': 'V9', 'map_enabled': True, 'map_workers': 0, 'map_priority': 'fifo99'},
                {'version': 'V9', 'map_enabled': True, 'map_workers': -1, 'map_priority': 'normal'},
                {'version': 'V9', 'map_enabled': True, 'map_workers': 'many', 'map_priority': 'normal'},
                {'version': 'V9', 'map_enabled': 'yes', 'map_workers': 0, 'map_priority': 'normal'},
                {'version': 'V9', 'map_workers': 0, 'map_priority': 'normal'}, {'version': 'V9'}):
        (slot / 'ivi.json').write_text(json.dumps(bad))
        assert run(slot, '--check').returncode != 0


def test_workers_follow_the_map_flag_and_stop_on_sigterm(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    cfg = {'version': 'V3', 'map_enabled': True, 'map_workers': 2, 'map_priority': 'normal'}
    (slot / 'ivi.json').write_text(json.dumps(cfg))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})
    try:
        def status(predicate, seconds=6):
            deadline = time.time() + seconds
            while time.time() < deadline:
                try:
                    data = json.loads((runtime / 'status.json').read_text())
                    if predicate(data):
                        return data
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            raise AssertionError('status condition not reached')
        assert status(lambda s: s['workers'] == 0 and not s['map_active'])['version'] == 'V3'
        (runtime / 'map_active').touch()
        assert status(lambda s: s['workers'] == 2 and s['map_active'])['priority'] == 'normal'
        (runtime / 'map_active').unlink()
        status(lambda s: s['workers'] == 0)
        (runtime / 'map_active').touch()
        data = status(lambda s: s['workers'] == 2)
        children = subprocess.run(['pgrep', '-P', str(app.pid)], capture_output=True, text=True).stdout.split()
        assert len(children) == 2
        app.terminate()
        assert app.wait(timeout=5) == 0
        time.sleep(0.3)
        alive = subprocess.run(['ps', '-p', ','.join(children), '-o', 'pid='], capture_output=True, text=True).stdout.split()
        assert alive == []                       # no orphaned CPU-burning workers
    finally:
        if app.poll() is None:
            app.kill()


def test_flag_content_decides_whether_the_map_is_active(tmp_path):
    from dashboard.ivi.ivi import flag_active
    flag = tmp_path / 'map_active'
    assert not flag_active(flag)                         # missing
    for content, expected in (('', True), ('1', True), ('1\n', True), ('0', False), ('0\n', False), (' 0 \n', False)):
        flag.write_text(content)
        assert flag_active(flag) is expected, repr(content)


def test_workers_stop_when_the_flag_content_changes_to_zero(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    (slot / 'ivi.json').write_text(json.dumps({'version': 'V3', 'map_enabled': True, 'map_workers': 2, 'map_priority': 'normal'}))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    (runtime / 'map_active').write_text('1')
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})

    def status(predicate, seconds=6):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                data = json.loads((runtime / 'status.json').read_text())
                if predicate(data):
                    return data
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError('status condition not reached')
    try:
        status(lambda s: s['workers'] == 2 and s['map_active'])
        (runtime / 'map_active').write_text('0')         # the file stays, the content says "map closed"
        status(lambda s: s['workers'] == 0 and not s['map_active'])
        (runtime / 'map_active').write_text('1')
        status(lambda s: s['workers'] == 2 and s['map_active'])
    finally:
        _stop_app(app)


def test_realtime_request_falls_back_visibly_without_privilege(tmp_path):
    if os.geteuid() == 0:
        pytest.skip('running as root; fallback not observable')
    slot = payload(tmp_path / 's', 'V3')
    (slot / 'ivi.json').write_text(json.dumps({'version': 'V3', 'map_enabled': True, 'map_workers': 1, 'map_priority': 'rr10'}))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    (runtime / 'map_active').touch()
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''},
                           stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(2)
        status = json.loads((runtime / 'status.json').read_text())
        assert status['priority'] == 'rr10' and status['workers'] == 1
        app.terminate()
        assert 'realtime priority not granted' in app.communicate(timeout=5)[1]
    finally:
        if app.poll() is None:
            app.kill()


def test_ivi_device_manages_its_own_service_with_a_long_verifier_deadline(monkeypatch, tmp_path):
    import types
    from capstone_ota.cluster import ClusterDevice
    from capstone_ota.ivi import IviDevice
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0))
    IviDevice(tmp_path)._service('restart')
    ClusterDevice(tmp_path)._service('restart')
    assert calls == [['systemctl', 'restart', 'capstone-ivi.service'], ['systemctl', 'restart', 'capstone-cluster.service']]
    assert IviDevice.verifier_timeout > ClusterDevice.verifier_timeout == 60


def test_cli_exposes_ivi_update():
    from capstone_ota import cli
    with pytest.raises(SystemExit):                       # argparse demands a validation mode
        cli.main(['ivi-update', 'http://127.0.0.1/x/'])


def test_navigation_position_follows_the_route_when_a_map_is_shipped(tmp_path):
    import json as _json
    route = tmp_path / 'map.json'
    route.write_text(_json.dumps({'route': {'p': [0, 0, 100, 0, 100, 100, 0, 100, 0, 0]}}))
    slot = payload(tmp_path / 's', 'V3', map_file=route)
    (slot / 'ivi.json').write_text(_json.dumps({'version': 'V3', 'map_enabled': True, 'map_workers': 0, 'map_priority': 'normal'}))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})
    try:
        deadline = time.time() + 6
        while time.time() < deadline and not (runtime / 'nav.json').exists():
            time.sleep(0.1)
        nav = _json.loads((runtime / 'nav.json').read_text())
        assert nav['speed'] == 0 and (nav['x'], nav['y']) == (0, 0) and 'heading' in nav
        assert _json.loads((runtime / 'status.json').read_text())['route'] is True
    finally:
        _stop_app(app)


def test_v2_has_no_map_so_the_map_flag_does_nothing(tmp_path):
    slot = payload(tmp_path / 's', 'V2')
    runtime = tmp_path / 'run'
    runtime.mkdir()
    (runtime / 'map_active').touch()                 # someone "opens the map" on a version that has none
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})
    try:
        deadline = time.time() + 6
        data = None
        while time.time() < deadline:
            try:
                data = json.loads((runtime / 'status.json').read_text())
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        assert data and data['version'] == 'V2' and data['map_enabled'] is False
        time.sleep(1.5)
        data = json.loads((runtime / 'status.json').read_text())
        assert data['map_active'] is False and data['workers'] == 0 and not (runtime / 'nav.json').exists()
        assert subprocess.run(['pgrep', '-P', str(app.pid)], capture_output=True).stdout == b''     # no CPU-burning children
    finally:
        _stop_app(app)


def test_ivi_initialization_preserves_the_signed_payload_tree(tmp_path):
    from capstone_ota.bundle import tree_hash
    slot = payload(tmp_path / 'readonly', 'V2')
    before = tree_hash(slot)
    assert run(slot, '--check').returncode == 0
    assert tree_hash(slot) == before


class _Stop(Exception):
    pass


def _run_in_process(monkeypatch, tmp_path, config, frames, bus_available=True, ticks=10):
    """Run ivi.main in-process with a fake CAN socket and a fake clock; returns (nav, status, opened)."""
    import socket
    import struct
    import types

    from dashboard.ivi import ivi
    (tmp_path / 'ivi.json').write_text(json.dumps(config))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    monkeypatch.setattr(ivi, 'ROOT', tmp_path)
    monkeypatch.setenv('CAPSTONE_IVI_RUN', str(runtime))
    monkeypatch.setattr(ivi.signal, 'signal', lambda *_: None)
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    reader.setblocking(False)
    for speed in frames:
        writer.send(struct.pack('=IB3x8s', 0x200, 8, int(speed * 10).to_bytes(2, 'big') + bytes(6)))
    opened = []
    monkeypatch.setattr(ivi, 'open_can', lambda ch: opened.append(ch) or (reader if bus_available else None))
    clock = {'t': 100.0, 'n': 0}

    def sleep(dt):
        clock['t'] += dt
        clock['n'] += 1
        if clock['n'] >= ticks:
            raise _Stop
    monkeypatch.setattr(ivi, 'time', types.SimpleNamespace(monotonic=lambda: clock['t'], time=lambda: 1.0, sleep=sleep))
    with pytest.raises(_Stop):
        ivi.main(['ivi.py'])
    read = lambda name: json.loads((runtime / name).read_text()) if (runtime / name).exists() else None  # noqa: E731
    return read('nav.json'), read('status.json'), opened


def test_v2_reads_can_and_writes_trip_info(monkeypatch, tmp_path):
    nav, status, opened = _run_in_process(monkeypatch, tmp_path, CONFIGS['V2'], [36.0])   # 36 km/h = 10 m/s
    assert opened and status['can'] is True and status['workers'] == 0 and status['route'] is False
    assert nav['speed'] == 36.0 and set(nav) == {'speed', 'distance', 'drive_s', 'updated'}
    assert nav['distance'] == pytest.approx(18.0, abs=0.1) and nav['drive_s'] == pytest.approx(1.8, abs=0.05)   # 9 intervals of 0.2 s


def test_v2_standing_still_does_not_count_driving_time(monkeypatch, tmp_path):
    nav, _, _ = _run_in_process(monkeypatch, tmp_path, CONFIGS['V2'], [0.3])
    assert nav['distance'] > 0 and nav['drive_s'] == 0


def test_vehicle_data_flag_controls_whether_v2_opens_can(monkeypatch, tmp_path):
    off = {**CONFIGS['V2'], 'vehicle_data': False}
    (tmp_path / 'off').mkdir()
    nav, status, opened = _run_in_process(monkeypatch, tmp_path / 'off', off, [36.0])
    assert opened == [] and nav is None and status['can'] is False
    legacy = {k: v for k, v in CONFIGS['V2'].items() if k != 'vehicle_data'}      # old ivi.json: behaves as before
    (tmp_path / 'old').mkdir()
    nav, status, opened = _run_in_process(monkeypatch, tmp_path / 'old', legacy, [36.0])
    assert opened == [] and nav is None and status['can'] is False


def test_can_unavailable_reports_false_and_does_not_crash(monkeypatch, tmp_path):
    nav, status, opened = _run_in_process(monkeypatch, tmp_path, CONFIGS['V2'], [], bus_available=False)
    assert opened and status['can'] is False and nav is None


def test_v3_nav_keeps_position_fields_and_gains_drive_time(monkeypatch, tmp_path):
    (tmp_path / 'map.json').write_text(json.dumps({'route': {'p': [0, 0, 1000, 0]}}))
    config = {**CONFIGS['V3'], 'map_workers': 0}
    nav, status, _ = _run_in_process(monkeypatch, tmp_path, config, [36.0])
    assert {'x', 'y', 'heading', 'speed', 'distance', 'drive_s', 'updated'} == set(nav) and nav['drive_s'] > 0
    assert status['can'] is True and status['route'] is True


def test_map_duty_is_an_optional_fraction_above_zero_up_to_one(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    base = {'version': 'V9', 'map_enabled': True, 'map_workers': 2, 'map_priority': 'normal'}
    for duty in (0.75, 0.01, 1, 1.0):
        (slot / 'ivi.json').write_text(json.dumps({**base, 'map_duty': duty}))
        assert run(slot, '--check').returncode == 0, duty
    for bad in (0, 0.0, -0.5, 1.2, 'x', True, None):
        (slot / 'ivi.json').write_text(json.dumps({**base, 'map_duty': bad}))
        assert run(slot, '--check').returncode != 0, bad
    (slot / 'ivi.json').write_text(json.dumps(base))          # older configs without the key keep working
    assert run(slot, '--check').returncode == 0


def test_duty_cycle_splits_the_period_into_busy_and_rest():
    from dashboard.ivi.ivi import MAP_PERIOD, duty_cycle
    busy, rest = duty_cycle(0.75)
    assert busy == pytest.approx(0.75 * MAP_PERIOD) and rest == pytest.approx(0.25 * MAP_PERIOD)
    assert duty_cycle(1.0) == (MAP_PERIOD, 0.0)


def _cpu_seconds(pid):
    fields = (open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1]).split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')


def test_a_worker_with_duty_half_uses_about_half_of_what_a_full_worker_uses():
    # Compared with a duty-1 worker running at the same moment: absolute CPU shares depend on how busy the machine is.
    ivi = os.path.join(os.path.dirname(__file__), '..', 'dashboard', 'ivi', 'ivi.py')
    half = subprocess.Popen([sys.executable, ivi, '--worker', 'normal', '0.5'])
    full = subprocess.Popen([sys.executable, ivi, '--worker', 'normal', '1.0'])
    try:
        time.sleep(0.5)
        first = (_cpu_seconds(half.pid), _cpu_seconds(full.pid))
        time.sleep(4.0)
        used_half, used_full = _cpu_seconds(half.pid) - first[0], _cpu_seconds(full.pid) - first[1]
    finally:
        for process in (half, full):
            process.kill()
            process.wait()
    assert used_full > 0.3, used_full                       # the machine gave the full worker something to compare with
    assert 0.3 <= used_half / used_full <= 0.7, (used_half, used_full)


def test_the_app_hands_map_duty_to_the_workers_it_starts(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    cfg = {'version': 'V3', 'map_enabled': True, 'map_workers': 1, 'map_priority': 'normal', 'map_duty': 0.5}
    (slot / 'ivi.json').write_text(json.dumps(cfg))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})
    try:
        (runtime / 'map_active').touch()
        deadline, children = time.time() + 6, []
        while time.time() < deadline and not children:
            children = subprocess.run(['pgrep', '-P', str(app.pid)], capture_output=True, text=True).stdout.split()
            time.sleep(0.1)
        assert len(children) == 1
        argv = open(f'/proc/{children[0]}/cmdline').read().rstrip('\0').split('\0')
        assert argv[-3:-1] == ['--worker', 'normal'] and float(argv[-1]) == 0.5
    finally:
        _stop_app(app)


def test_stopping_the_app_through_the_test_helper_leaves_no_busy_workers(tmp_path):
    slot = payload(tmp_path / 's', 'V3')
    cfg = {'version': 'V3', 'map_enabled': True, 'map_workers': 2, 'map_priority': 'normal'}
    (slot / 'ivi.json').write_text(json.dumps(cfg))
    runtime = tmp_path / 'run'
    runtime.mkdir()
    app = subprocess.Popen([str(slot / 'bin/app')], env={**os.environ, 'CAPSTONE_IVI_RUN': str(runtime), 'CAPSTONE_IVI_CAN': ''})
    (runtime / 'map_active').touch()
    deadline, children = time.time() + 6, []
    while time.time() < deadline and len(children) < 2:
        children = subprocess.run(['pgrep', '-P', str(app.pid)], capture_output=True, text=True).stdout.split()
        time.sleep(0.1)
    assert len(children) == 2
    _stop_app(app)
    time.sleep(0.3)
    alive = subprocess.run(['ps', '-p', ','.join(children), '-o', 'pid='], capture_output=True, text=True).stdout.split()
    assert alive == []
