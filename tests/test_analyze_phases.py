"""Per-phase latency report used by the Gate 0 CPU-load experiment."""
import pytest

from bench import drive_cycle as dc
from bench.analyze_phases import phase_report
from bench.trace_sim import simulate
from capstone_ota.monitor import trace

T0 = 1000.0


def paired(mode):
    return trace.pair_frames(simulate(dc.build_profile(dc.SYNTHETIC_CYCLE), mode=mode, seed=1, start=40.0, t0=T0))


def test_report_separates_quiet_and_loaded_phases():
    phases = [{'name': 'idle', 'load': 0, 't0': T0 + 2, 't1': T0 + 36},
              {'name': 'load', 'load': 4, 't0': T0 + 42, 't1': T0 + 90}]
    quiet, loaded = phase_report(paired('delay'), phases, margin=1.0)
    assert quiet['name'] == 'idle' and loaded['load'] == 4
    assert quiet['lat_p99_ms'] < 30 and loaded['lat_p99_ms'] > 100
    assert quiet['n_pairs'] > 250 and loaded['n_pairs'] > 400


def test_report_counts_skipped_updates():
    phases = [{'name': 'stalls', 'load': 8, 't0': T0 + 41, 't1': T0 + 100}]
    row = phase_report(paired('freeze'), phases, margin=0.5)[0]
    assert row['skip_ratio'] > 0.05 and row['max_gap_ms'] > 350


def test_empty_phase_reports_none_not_zero():
    row = phase_report(paired('normal'), [{'name': 'far', 'load': 0, 't0': T0 + 5000, 't1': T0 + 5030}])[0]
    assert row['n_pairs'] == 0 and row['lat_p50_ms'] is None


def test_margin_must_leave_a_window():
    with pytest.raises(ValueError):
        phase_report(paired('normal'), [{'name': 'x', 'load': 0, 't0': T0, 't1': T0 + 3}], margin=2.0)
