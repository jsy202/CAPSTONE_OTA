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
    assert any('Pi3' in line['text'] and 'lost' in line['text'] for line in gone['log'])
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
    assert any('confirmed' in t and 'freeze' in t for t in texts) and describe_event('recover') in texts


def test_ivi_version_change_is_logged_and_log_is_bounded(tmp_path):
    ivi = {'v': {'version': 'V3', 'workers': 4, 'map_active': True}}
    feeder = make(tmp_path, ivi=lambda: ivi['v'])
    feeder.collect()
    ivi['v'] = {'version': 'V2', 'workers': 1, 'map_active': True}
    texts = [l['text'] for l in feeder.collect()['log']]
    assert any('V2' in t for t in texts)
    for i in range(40):
        ivi['v'] = {'version': 'V%d' % (i % 2 + 2), 'workers': 0, 'map_active': False}
        feeder.collect()
    assert len(feeder.collect()['log']) <= 14


def test_unknown_events_are_described_not_dropped():
    assert describe_event('something_new') == 'Device: something_new'
    assert 'Rollback' in describe_event('recover') and 'Trial' in describe_event('trial')


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
