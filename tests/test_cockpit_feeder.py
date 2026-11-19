"""Cockpit data feeder: assembles one JSON for the single-monitor display from independent sources."""
import json

from bench.cockpit_feeder import Feeder, describe_event


def make(tmp_path, **sources):
    defaults = dict(cluster=lambda: {'valid': True, 'model_speed': 12.5, 'rejected': 0},
                    pi3=lambda: {'seq': 1, 'state': 'observing', 'cls': 'normal', 'fired': [], 'monitored_s': 5.0,
                                 'bus_stopped_s': 0.0, 'bus_speed': 125, 'last_window': {'lat_p99_ms': 3.1, 'skip_ratio': 0.0}},
                    ota=lambda: {'stage': 'committed', 'version': '2.0.0', 'candidate': None,
                                 'slot_view': {'A': {'version': '2.0.0', 'role': 'committed'}, 'B': {'version': None, 'role': 'retained'}}},
                    events=lambda: [], ivi=lambda: {'version': 'V2', 'workers': 0, 'map_active': False},
                    ivi_ui=lambda: 'file:///x/slots/A/ui/Ivi.qml')
    defaults.update(sources)
    return Feeder(tmp_path, clock=lambda: 1000.0, **defaults)


def test_collect_merges_sources_and_marks_missing_ones_as_none(tmp_path):
    data = make(tmp_path).collect()
    assert data['cluster']['model_speed'] == 12.5 and data['pi3']['state'] == 'observing'
    assert data['ota']['version'] == '2.0.0' and data['ivi']['version'] == 'V2' and data['ivi_ui'].endswith('Ivi.qml')
    gone = make(tmp_path, cluster=lambda: None, pi3=lambda: None).collect()
    assert gone['cluster'] is None and gone['pi3'] is None
    assert any(line['text'] == '모니터: 신호 끊김' for line in gone['log'])
    json.dumps(data)


def test_state_changes_and_device_events_become_log_lines_once(tmp_path):
    state = {'pi3': {'seq': 1, 'state': 'observing', 'cls': 'normal', 'fired': [], 'monitored_s': 1, 'bus_stopped_s': 0, 'bus_speed': 0}}
    events = [{'time': 1.0, 'event': 'trial'}]
    feeder = make(tmp_path, pi3=lambda: state['pi3'], events=lambda: events)
    first = feeder.collect()
    assert [l['text'] for l in first['log']].count(describe_event('trial')) == 1
    again = feeder.collect()
    assert len(again['log']) == len(first['log'])                       # nothing new, nothing repeated
    state['pi3'] = dict(state['pi3'], state='confirmed', cls='freeze', fired=['latency_p99'])
    events.append({'time': 2.0, 'event': 'recover'})
    third = feeder.collect()
    texts = [l['text'] for l in third['log']]
    assert '모니터: 한계 초과 확정 (응답 멈춤)' in texts and describe_event('recover') in texts


def test_ivi_version_change_is_logged_and_log_is_bounded(tmp_path):
    ivi = {'v': {'version': 'V3', 'workers': 4, 'map_active': True}}
    feeder = make(tmp_path, ivi=lambda: ivi['v'])
    feeder.collect()
    ivi['v'] = {'version': 'V2', 'workers': 1, 'map_active': True}
    texts = [l['text'] for l in feeder.collect()['log']]
    assert '화면 앱 V2 실행 중' in texts
    for i in range(40):
        ivi['v'] = {'version': 'V%d' % (i % 2 + 2), 'workers': 0, 'map_active': False}
        feeder.collect()
    assert len(feeder.collect()['log']) <= 14


def test_unknown_events_are_described_not_dropped():
    assert describe_event('something_new') == '장치: something_new'
    assert describe_event('download_and_verify') == '업데이트: 서명된 릴리스를 내려받는 중'
    assert describe_event('trial') == '시운전: 새 버전 설치, 시험 중'
    assert describe_event('committed') == '확정: 새 버전 최종 승인'
    assert describe_event('recover') == '롤백: 이전 버전으로 되돌리는 중'
    assert describe_event('recovery_verified') == '롤백: 이전 버전 검사 통과'
    assert describe_event('recovery_failed') == '롤백 실패: 수동 점검 필요'


def test_monitor_state_lines_are_korean(tmp_path):
    state = {'pi3': {'state': 'observing', 'cls': 'normal'}}
    feeder = make(tmp_path, pi3=lambda: state['pi3'])
    assert '모니터: 정상 관측' in [l['text'] for l in feeder.collect()['log']]
    state['pi3'] = {'state': 'suspect', 'cls': 'freeze'}
    assert '모니터: 징후 포착 (응답 멈춤)' in [l['text'] for l in feeder.collect()['log']]


def test_write_is_atomic_and_world_readable(tmp_path):
    feeder = make(tmp_path)
    feeder.write()
    path = tmp_path / 'data.json'
    assert json.loads(path.read_text())['cluster']['valid'] is True
    assert path.stat().st_mode & 0o044 == 0o044 and not list(tmp_path.glob('.*.tmp'))


def test_tap_is_passed_through_and_a_failing_tap_is_unknown(tmp_path):
    assert make(tmp_path, tap=lambda: {'delay_ms': 3.2, 'max_ms': 5.0, 'n': 19}).collect()['tap']['delay_ms'] == 3.2
    def broken():
        raise OSError('socket gone')
    assert make(tmp_path, tap=broken).collect()['tap'] is None


def test_capture_reads_external_framebuffer_without_requesting_qt_render(tmp_path):
    from bench.cockpit_feeder import capture_cluster
    import os
    import selectors
    import shutil
    import socket
    import subprocess
    import threading
    import pytest
    Image = pytest.importorskip('PIL.Image')
    if not shutil.which('Xvfb'):
        pytest.skip('Xvfb is needed for a real framebuffer capture')
    read_fd, write_fd = os.pipe()
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '32x24x24',
                             '-nolisten', 'tcp', '-ac'], pass_fds=(write_fd,),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            assert selector.select(5), 'Xvfb did not start'
            display = ':' + os.read(read_fd, 100).decode().strip()
        path = tmp_path / 'model.sock'
        requests = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path)); server.listen(1)
            def serve():
                client, _ = server.accept()
                with client:
                    requests.append(json.loads(client.makefile('rb').readline()))
                    client.sendall(json.dumps({'valid': True, 'model_speed': 65, 'rejected': 0}).encode() + b'\n')
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            observed = capture_cluster(str(path), display, tmp_path, 7)
            worker.join(2)
        assert requests[0]['op'] == 'snapshot'  # capture would synchronously render inside the ECU
        assert observed == {'valid': True, 'model_speed': 65, 'rejected': 0, 'rev': 7}
        with Image.open(tmp_path / 'cluster.bmp') as image:
            assert image.size == (32, 24) and image.format == 'BMP'
        assert (tmp_path / 'cluster.bmp').stat().st_mode & 0o044 == 0o044
    finally:
        os.close(read_fd)
        xvfb.terminate(); xvfb.wait(timeout=5)


def test_download_bytes_reach_the_display_data(tmp_path):
    ota = lambda: {'stage': 'download', 'received': 10, 'total': 40}
    assert make(tmp_path, ota=ota).collect()['ota'] == {'stage': 'download', 'received': 10, 'total': 40}


def test_port_events_from_the_control_side_appear_in_the_log_once(tmp_path):
    rows = [{'t': '06:11:02', 'text': '진단 포트 7400 열림'}]
    feeder = make(tmp_path, extra=lambda: list(rows))
    assert any(line['text'] == '진단 포트 7400 열림' for line in feeder.collect()['log'])
    rows.append({'t': '06:11:20', 'text': '진단 포트 닫힘 (사고 보고)'})
    texts = [line['text'] for line in feeder.collect()['log']]
    assert texts.count('진단 포트 7400 열림') == 1 and texts.count('진단 포트 닫힘 (사고 보고)') == 1
    assert [line['t'] for line in feeder.collect()['log'] if '진단' in line['text']] == ['06:11:02', '06:11:20']  # their own time


def test_every_event_the_device_writes_has_korean_text_and_no_raw_identifier():
    import re
    import bench.cockpit_feeder as feeder
    device_events = ['committed', 'download_and_verify', 'recover', 'recovery_failed', 'repair_installed',
                     'repair_rejected', 'repair_started', 'service_stopped_after_recovery_failure', 'trial']
    for name in device_events:
        text = describe_event(name)
        assert not re.search(r'[a-z]+_[a-z]+', text), (name, text)  # a raw identifier must never reach the screen
        assert re.search('[가-힣]', text), (name, text)
    assert feeder.CLASS_TEXT['freeze'] == '응답 멈춤' and feeder.CLASS_TEXT['delay'] == '응답 지연'


def test_the_monitor_log_line_names_the_failure_class_in_korean(tmp_path):
    pi3 = {'seq': 1, 'state': 'confirmed', 'cls': 'freeze', 'fired': [], 'monitored_s': 5, 'bus_stopped_s': 0, 'bus_speed': 0}
    log = make(tmp_path, pi3=lambda: pi3).collect()['log']
    assert any('한계 초과 확정' in line['text'] and '응답 멈춤' in line['text'] and 'freeze' not in line['text'] for line in log)


def test_a_preview_path_overrides_the_installed_slot_for_the_ivi_screen(tmp_path):
    from bench.cockpit_feeder import resolve_ivi_ui
    slot = tmp_path / "current"
    (slot / "ui").mkdir(parents=True)
    (slot / "ui" / "Ivi.qml").write_text("Item {}")
    preview = tmp_path / "preview" / "ui"
    preview.mkdir(parents=True)
    (preview / "Ivi.qml").write_text("Item {}")
    assert resolve_ivi_ui(slot, None) == "file://" + str(slot.resolve() / "ui/Ivi.qml")   # normal: the installed slot's own UI
    assert resolve_ivi_ui(slot, preview) == "file://" + str(preview / "Ivi.qml")            # preview: only when asked for
    assert resolve_ivi_ui(tmp_path / "none", None) is None                                  # nothing installed: no screen
