import pytest
from bench import load_phases as lp


def test_validate_rejects_bad_schedules():
    for bad in ([{'name': 'a', 'load': -1, 't0': 0, 't1': 1}], [{'name': 'a', 'load': 1, 't0': 5, 't1': 5}],
                [{'name': 'a', 'load': 1, 't0': 0, 't1': 10}, {'name': 'b', 'load': 1, 't0': 5, 't1': 20}],
                [{'name': 'a', 'load': 99, 't0': 0, 't1': 1}], [{'name': 'a'}]):
        with pytest.raises(ValueError):
            lp.validate(bad)
    assert lp.validate(lp.DEFAULT)


def test_run_spawns_and_kills_the_right_number_of_workers():
    now = {'t': 0.0}

    class Worker:
        killed = waited = False
        def kill(self): Worker.killed = True; self.dead = True
        def wait(self): Worker.waited = True

    spawned = []
    def spawn(policy='normal'):
        spawned.append(Worker()); return spawned[-1]
    def sleep(s): now['t'] += s
    result = lp.run([{'name': 'a', 'load': 2, 't0': 0, 't1': 2}, {'name': 'b', 'load': 0, 't0': 2, 't1': 4}],
                    clock=lambda: now['t'], sleep=sleep, spawn=spawn, sample=lambda: {'throttled': 'x'})
    assert len(spawned) == 2 and all(w.dead for w in spawned)
    assert [p['name'] for p in result['phases']] == ['a', 'b']
    assert result['phases'][0]['t1'] <= result['phases'][1]['t0'] + 0.1
    assert result['env'] and result['env'][0]['phase'] == 'a'


def test_policy_is_optional_and_restricted():
    ok = [{'name': 'a', 'load': 1, 't0': 0, 't1': 5, 'policy': 'rr10'}]
    assert lp.validate(ok)
    with pytest.raises(ValueError):
        lp.validate([{'name': 'a', 'load': 1, 't0': 0, 't1': 5, 'policy': 'fifo99'}])


def test_run_passes_the_phase_policy_to_spawn():
    now, seen = {'t': 0.0}, []
    class W:
        def kill(self): pass
        def wait(self): pass
    def spawn(policy='normal'):
        seen.append(policy); return W()
    lp.run([{'name': 'a', 'load': 2, 't0': 0, 't1': 1, 'policy': 'nice-10'}], clock=lambda: now['t'],
           sleep=lambda s: now.update(t=now['t'] + s), spawn=spawn, sample=lambda: {})
    assert seen == ['nice-10', 'nice-10']
