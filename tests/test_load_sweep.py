"""CPU load sweep: which load makes the cluster late without silencing it."""
import resource
import subprocess
import sys
import time

from bench import load_sweep


def test_load_script_is_valid_python_and_realtime_only_when_asked():
    normal = load_sweep.load_script(workers=2, priority="normal", duty=1.0, period=0.1, seconds=5)
    realtime = load_sweep.load_script(workers=2, priority="rr10", duty=1.0, period=0.1, seconds=5)
    compile(normal, "normal", "exec")
    compile(realtime, "rr", "exec")
    assert "SCHED_RR" not in normal and "SCHED_RR" in realtime


def test_load_stops_by_itself_and_burns_about_duty_times_workers_cpu():
    script = load_sweep.load_script(workers=2, priority="normal", duty=0.5, period=0.1, seconds=2)
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    subprocess.run([sys.executable, "-c", script], timeout=15, check=True)
    elapsed = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    assert 1.8 <= elapsed <= 4.5                       # ends on its own (safety: a forgotten load must not stay)
    assert 1.4 <= cpu <= 2.7                           # 2 workers x 2 s x 50% duty = 2 CPU seconds


def row(p99, skip=0.0, gap=100.0, pairs=1500):
    return {"lat_p99_ms": p99, "skip_ratio": skip, "max_gap_ms": gap, "n_pairs": pairs, "n_sources": pairs}


def test_a_late_but_talking_cluster_is_moderate():
    assert load_sweep.classify(row(350.0), screen_ok=1.0) == "moderate"


def test_below_the_limit_is_light_and_far_above_is_severe():
    assert load_sweep.classify(row(40.0), screen_ok=1.0) == "light"
    assert load_sweep.classify(row(2900.0), screen_ok=1.0) == "severe"


def test_a_silent_cluster_or_a_dead_screen_feed_is_lost_whatever_the_latency():
    assert load_sweep.classify(row(300.0, skip=0.4), screen_ok=1.0) == "lost"
    assert load_sweep.classify(row(300.0, gap=4000.0), screen_ok=1.0) == "lost"
    assert load_sweep.classify(row(300.0), screen_ok=0.2) == "lost"      # the cockpit would print "no signal"
    assert load_sweep.classify(row(None, pairs=0), screen_ok=1.0) == "lost"
