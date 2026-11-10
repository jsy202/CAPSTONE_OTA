"""Run on the Cockpit board: step CPU load (N busy processes) through timed phases and log it.

Stdlib only. Phase boundaries and board health samples (throttling, temperature, clock) are written
as epoch seconds so a trace taken elsewhere can be split by phase. This is a Gate 0 stand-in for
the IVI feature's CPU use, not the feature itself.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

DEFAULT = [{'name': 'idle', 'load': 0, 't0': 0, 't1': 30}, {'name': 'load2', 'load': 2, 't0': 30, 't1': 60},
           {'name': 'load4', 'load': 4, 't0': 60, 't1': 90}, {'name': 'load8', 'load': 8, 't0': 90, 't1': 120},
           {'name': 'idle2', 'load': 0, 't0': 120, 't1': 150}]


POLICIES = ('normal', 'nice-10', 'rr10')   # nice-10 / rr10 need root; rr10 is a realtime SCHED_RR priority


def validate(schedule):
    last = 0
    for phase in schedule:
        if (not {'name', 'load', 't0', 't1'} <= set(phase) <= {'name', 'load', 't0', 't1', 'policy'}
                or type(phase['load']) is not int or not 0 <= phase['load'] <= 16
                or phase['t0'] < last or phase['t1'] <= phase['t0']
                or phase.get('policy', 'normal') not in POLICIES):
            raise ValueError('invalid phase schedule')
        last = phase['t1']
    return schedule


def default_spawn(policy='normal'):
    def prepare():
        if policy == 'nice-10':
            os.nice(-10)
        elif policy == 'rr10':
            os.sched_setscheduler(0, os.SCHED_RR, os.sched_param(10))
    return subprocess.Popen([sys.executable, '-c', 'while True: pass'], preexec_fn=prepare)


def health():
    def vc(*args):
        try:
            return subprocess.run(['vcgencmd', *args], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {'throttled': vc('get_throttled'), 'temp': vc('measure_temp'), 'arm_clock': vc('measure_clock', 'arm')}


def run(schedule, clock=time.time, sleep=time.sleep, spawn=None, sample=health):
    spawn = spawn or default_spawn
    start = clock()
    phases, env, workers = [], [], []
    try:
        for phase in validate(schedule):
            while clock() - start < phase['t0']:
                sleep(0.05)
            workers = [spawn(phase.get('policy', 'normal')) for _ in range(phase['load'])]
            began = clock()
            next_sample = began
            while clock() - start < phase['t1']:
                if clock() >= next_sample:
                    env.append({'t': clock(), 'phase': phase['name'], **sample()})
                    next_sample += 5
                sleep(0.05)
            phases.append({'name': phase['name'], 'load': phase['load'], 'policy': phase.get('policy', 'normal'),
                           't0': began, 't1': clock()})
            for worker in workers:
                worker.kill()
            for worker in workers:
                worker.wait()
            workers = []
    finally:
        for worker in workers:
            worker.kill()
    return {'phases': phases, 'env': env}


if __name__ == '__main__':
    out = sys.argv[1]
    schedule = json.loads(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT
    result = run(schedule)
    with open(out, 'w') as stream:
        json.dump(result, stream)
    print(json.dumps({'phases': len(result['phases']), 'out': out}))
