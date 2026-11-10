"""Mock IVI payload: config validation, check mode, and map-driven worker lifecycle."""
import json
import os
import subprocess
import sys
import time

import pytest

from dashboard.ivi.build import CONFIGS, payload


def run(slot, *args, env=None, timeout=10):
    return subprocess.run([str(slot / 'bin/app'), *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, 'CAPSTONE_IVI_CAN': '', **(env or {})})


def test_payload_layout_and_versions(tmp_path):
    mapfile = tmp_path / 'map.json'
    mapfile.write_text('{"route": {"p": [0, 0, 1, 1]}}')
    for version in ('V2', 'V3'):
        slot = payload(tmp_path / version, version, map_file=mapfile)
        assert (slot / 'bin/app').is_file() and os.access(slot / 'bin/app', os.X_OK)
        assert json.loads((slot / 'ivi.json').read_text()) == CONFIGS[version]
    # V2 is the head unit WITHOUT a map; V3 adds the map and its tile-prefetch workers.
    assert CONFIGS['V2'] == {'version': 'V2', 'map_enabled': False, 'map_workers': 0, 'map_priority': 'normal'}
    assert CONFIGS['V3'] == {'version': 'V3', 'map_enabled': True, 'map_workers': 'cores', 'map_priority': 'rr10'}
    assert not (tmp_path / 'V2/map.json').exists() and (tmp_path / 'V3/map.json').exists()


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
        app.kill()
        app.wait()


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
        app.kill()
        app.wait()


def test_ivi_initialization_preserves_the_signed_payload_tree(tmp_path):
    from capstone_ota.bundle import tree_hash
    slot = payload(tmp_path / 'readonly', 'V2')
    before = tree_hash(slot)
    assert run(slot, '--check').returncode == 0
    assert tree_hash(slot) == before
