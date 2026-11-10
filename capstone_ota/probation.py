"""Probation supervisor (Cockpit board): decide pass or deferred rollback of a freshly installed app.

Runs as the Device's external verifier during the trial phase. It pulls the Verifier board's verdict
(the Verifier never gets access to this board), passes only after enough *monitored* time, and on a
confirmed anomaly (or an unmonitorable update) waits for a safe window before returning a failing
verdict, which makes the existing recovery path roll back. The safe window needs this board's own
cluster observation AND the Verifier's bus observation to both report standstill. If the Verifier
is blind or stale it degrades to this board's observation alone and says so.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
import time
import uuid


class Probation:
    def __init__(self, fetch_verdict, reset_monitor, cluster_state, clock=time.monotonic, sleep=time.sleep,
                 probation_s: float = 60.0, safe_s: float = 5.0, max_wait_s: float = 600.0,
                 max_total_s: float | None = None, poll_s: float = 2.0, stale_s: float = 6.0, obs_grace: float = 3.0, trace=None,
                 wait_poll_s: float | None = None):
        self.fetch, self.reset_monitor, self.cluster = fetch_verdict, reset_monitor, cluster_state
        self.clock, self.sleep = clock, sleep
        self.probation_s, self.safe_s, self.max_wait_s = probation_s, safe_s, max_wait_s
        self.max_total_s = max_total_s if max_total_s is not None else probation_s * 4
        self.poll_s, self.stale_s, self.obs_grace = poll_s, stale_s, obs_grace
        self.trace = trace or (lambda row: None)
        self.wait_poll_s = wait_poll_s if wait_poll_s is not None else min(poll_s, 1.0)
        self._cluster_obs = None                 # (time, stopped) of the last answer from the cluster
        self._seq, self._fresh_at = None, None

    def _try(self, call):
        try:
            return call()
        except Exception:                                     # a failing observer is "unknown", never a pass
            return None

    def _poll(self):
        """Latest Pi3 verdict and whether its sequence number advanced recently (liveness)."""
        verdict, now = self._try(self.fetch), self.clock()
        if isinstance(verdict, dict) and verdict.get('seq') != self._seq:
            self._seq, self._fresh_at = verdict.get('seq'), now
        fresh = isinstance(verdict, dict) and self._fresh_at is not None and now - self._fresh_at <= self.stale_s
        return (verdict if isinstance(verdict, dict) else None), fresh

    @staticmethod
    def _row(passed, verdict, **extra):
        row = {'passed': passed, 'state': None, 'cls': None, 'fired': [], 'monitored_s': None}
        if verdict:
            row.update({k: verdict.get(k) for k in ('state', 'cls', 'fired', 'monitored_s', 'confirm_t', 'windows')})
        row.update(extra)
        return row

    def run(self) -> dict:
        start = self.clock()
        nonce = uuid.uuid4().hex                      # proves the reset was applied: Pi3 must echo it back
        self.reset_monitor(nonce)
        acknowledged, last = False, None
        while True:
            verdict, fresh = self._poll()
            last = verdict or last
            if verdict and verdict.get('reset_nonce') == nonce:
                acknowledged = True
            blind = not (fresh and acknowledged) or (verdict or {}).get('state') == 'blind'
            if acknowledged and verdict and verdict.get('state') == 'confirmed':
                return self._rollback(verdict, f"confirmed {verdict.get('cls')} ({', '.join(verdict.get('fired', []))})")
            if not blind and verdict.get('monitored_s', 0) >= self.probation_s:
                return {'passed': True, 'observed': [self._row(True, verdict)],
                        'probation': {'elapsed_s': round(self.clock() - start, 1), 'monitored_s': verdict['monitored_s']}}
            if self.clock() - start >= self.max_total_s:
                return self._rollback(last, 'unmonitored: monitor was blind or stale for too long')
            self.sleep(self.poll_s)

    def _rollback(self, verdict, reason: str) -> dict:
        begin, safe_since = self.clock(), None
        while self.clock() - begin <= self.max_wait_s:
            cluster, (monitor, fresh) = self._try(self.cluster), self._poll()
            moment = self.clock()
            if cluster is not None:              # a starved cluster answers only some requests: remember the last answer,
                self._cluster_obs = (moment, bool(cluster.get('valid') is True and cluster.get('model_speed') == 0))
            # ...but it expires after obs_grace, and any answer that says "moving" resets it immediately.
            own = bool(self._cluster_obs and moment - self._cluster_obs[0] <= self.obs_grace and self._cluster_obs[1])
            watching = bool(monitor and fresh and monitor.get('state') != 'blind')
            bus = (not watching) or monitor.get('bus_stopped_s', 0) >= self.safe_s     # degrade if blind
            self.trace({'t': round(moment, 2), 'cluster': cluster, 'own_stopped': own, 'watching': watching,
                        'pi3_state': monitor.get('state') if monitor else None,
                        'pi3_bus_stopped_s': monitor.get('bus_stopped_s') if monitor else None, 'bus_stopped': bus,
                        'safe_since': None if safe_since is None else round(safe_since, 2)})
            if own and bus:
                safe_since = self.clock() if safe_since is None else safe_since
                if self.clock() - safe_since >= self.safe_s:
                    return {'passed': False, 'error': reason + '; rolled back in safe window' +
                            ('' if watching else ' (monitor blind: standstill judged by cockpit alone)'),
                            'observed': [self._row(False, verdict)]}
            else:
                safe_since = None
            self.sleep(self.wait_poll_s)
        return {'passed': False, 'error': reason + f'; safe_window_timeout after {self.max_wait_s:g}s, rolling back anyway',
                'observed': [self._row(False, verdict)]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi3-ssh', required=True, help='JSON argv list for ssh to the Verifier board')
    parser.add_argument('--verdict-path', required=True)
    parser.add_argument('--reset-path', required=True)
    parser.add_argument('--cluster-socket', default='', help='unused; kept for compatibility (standstill now comes from the local bus)')
    parser.add_argument('--trace-file', help='append one JSON line per safe-window decision')
    parser.add_argument('--probation', type=float, default=60.0)
    parser.add_argument('--safe', type=float, default=5.0)
    parser.add_argument('--max-wait', type=float, default=600.0)
    parser.add_argument('--max-total', type=float)
    parser.add_argument('--poll', type=float, default=2.0)
    parser.add_argument('--obs-grace', type=float, default=3.0)
    parser.add_argument('slot')
    args = parser.parse_args(argv)
    ssh = json.loads(args.pi3_ssh)
    if not isinstance(ssh, list) or not ssh or not all(isinstance(a, str) for a in ssh):
        raise SystemExit('--pi3-ssh must be a JSON array of strings')

    def remote(command):
        done = subprocess.run([*ssh, command], capture_output=True, text=True, timeout=10)
        if done.returncode:
            raise OSError(done.stderr[-200:])
        return done.stdout

    from .monitor.tap import SpeedTracker, run as run_tap
    tracker = SpeedTracker()
    running = {'now': True}
    threading.Thread(target=run_tap, args=('can0', tracker, lambda: running['now']), daemon=True).start()

    def cluster():
        """Standstill as seen on THIS board's own bus controller: independent of the Verifier's controller and,
        unlike the cluster's control socket, still answerable while the cluster is starved of CPU."""
        return tracker.snapshot(time.time())

    trace_file = open(args.trace_file, 'a') if args.trace_file else None

    def trace_row(row):
        if trace_file:
            trace_file.write(json.dumps(row) + '\n')
            trace_file.flush()

    cache = {'verdict': None, 'at': 0.0}
    done = {'now': False}

    def refresh():                            # remote reads are slow: keep them out of the decision loop
        while not done['now']:
            try:
                cache.update(verdict=json.loads(remote('cat ' + shlex.quote(args.verdict_path))), at=time.monotonic())
            except Exception:
                pass
            time.sleep(0.3)
    threading.Thread(target=refresh, daemon=True).start()

    def cached_verdict():
        if cache['verdict'] is None or time.monotonic() - cache['at'] > 8:
            raise OSError('no recent verdict from the Verifier')
        return cache['verdict']

    result = Probation(cached_verdict, lambda nonce: remote('printf %s ' + shlex.quote(nonce) + ' > ' + shlex.quote(args.reset_path + '.tmp') + ' && mv ' + shlex.quote(args.reset_path + '.tmp') + ' ' + shlex.quote(args.reset_path)), cluster,
                       probation_s=args.probation, safe_s=args.safe, max_wait_s=args.max_wait,
                       max_total_s=args.max_total, poll_s=args.poll, obs_grace=args.obs_grace, trace=trace_row,
                       wait_poll_s=0.25).run()
    done['now'] = True
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
