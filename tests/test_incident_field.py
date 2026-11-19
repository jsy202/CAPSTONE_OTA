"""bench.incident_field: what Pi1 sends must be exactly what Pi2's pinned gate allows (no board needed)."""
import shlex

from bench import incident_field as field
from capstone_ota import ota_gate
from capstone_ota.incident_responder import IncidentResponder
from capstone_ota.relay import target_command


def _remote(argv):
    return "cd " + shlex.quote(field.RUNTIME) + " && " + shlex.join([*argv, "http://" + field.PI1_ETH + ":4321"])


def test_the_gate_allows_the_update_pi1_sends():
    trial = field.forced_failure(25)
    config = field.gate_config(trial, field.passing_recheck())
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial))), config) is not None


def test_the_gate_allows_the_repair_pi1_sends():
    trial = field.forced_failure(25)
    config = field.gate_config(trial, field.passing_recheck())
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = field.target_for(trial)  # the update's check differs from the repair's re-check
    ssh = responder._repair_command("http://" + field.PI1_ETH + ":4321")
    assert ota_gate.allowed(ssh[-1], config) is not None


def test_the_gate_refuses_an_update_with_another_check():
    config = field.gate_config(field.forced_failure(25), field.passing_recheck())
    other = field.forced_failure(1)
    assert ota_gate.allowed(_remote(target_command(field.target_for(other))), config) is None


def test_the_gate_allows_the_real_probation_update_and_its_recheck():
    trial = field.probation_command("172.20.10.4")
    recheck = field.probation_command("172.20.10.4", probation=20, max_total=120, trace="recheck-trace.jsonl")
    config = field.gate_config(trial, recheck)
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial, recheck))), config) is not None
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = field.target_for(trial, recheck)
    assert ota_gate.allowed(responder._repair_command("http://" + field.PI1_ETH + ":4321")[-1], config) is not None


def test_probation_runs_on_the_instrument_cores_and_reads_the_monitor_files():
    command = field.probation_command("172.20.10.4")
    assert command[:3] == ["taskset", "-c", "1-3"]  # the cluster owns core 0
    assert command[command.index("--verdict-path") + 1].endswith("/monitor/verdict.json")
    assert command[command.index("--reset-path") + 1].endswith("/monitor/monitor.reset")
    assert "otauser@172.20.10.4" in command[command.index("--pi3-ssh") + 1]


def test_health_needs_a_fresh_verdict_that_measured_the_cluster_answer():
    import time
    fresh = {"frames": 40, "state": "observing", "now": time.time(), "recent_delay_ms": 3.2}
    assert field.healthy(fresh)
    assert not field.healthy(dict(fresh, recent_delay_ms=None))   # no 0x300 answer paired yet
    assert not field.healthy(dict(fresh, now=time.time() - 60))   # stale
    assert not field.healthy(dict(fresh, state="blind"))
    assert not field.healthy(dict(fresh, state="suspect"))        # already degrading before the update starts
    assert not field.healthy(dict(fresh, state="confirmed"))
    assert not field.healthy(None)


def test_the_cockpit_feeder_reads_the_same_monitor_files_as_the_probation_and_stays_off_core_0():
    command = field.feeder_command("172.20.10.4")
    assert command[:3] == ["taskset", "-c", "1-3"]  # the cluster owns core 0; the display must not starve it
    assert command[command.index("--verdict-path") + 1].endswith("/monitor/verdict.json")
    assert "otauser@172.20.10.4" in command[command.index("--pi3-ssh") + 1]
    assert command[command.index("--cluster-socket") + 1] == "/run/capstone-cluster/model.sock"


def test_the_cockpit_window_is_shown_on_the_desktop_session_of_the_connected_monitor():
    command = field.cockpit_ui_command()
    assert "QT_QPA_PLATFORM=wayland" in command and "WAYLAND_DISPLAY=wayland-0" in command
    assert "taskset -c 1-3" in command and command.rstrip().endswith("& echo started")
    assert "< /dev/null" in command  # no `cd ... &` and no inherited stdin: either one hangs the ssh channel


def test_gateway_log_entries_become_short_port_lines_for_the_cockpit():
    sent = {"event": "request", "source": "172.20.10.14:34178", "slot": "A", "size": 210660, "forwarded": True, "response": "OK"}
    assert field.port_line(sent) == "진단 포트 · 슬롯 A 이미지 수신 (172.20.10.14)"
    assert field.port_line({"event": "gateway_closed", "reason": "device reported recovery_failed"}) == "진단 포트 닫힘 · 이후 요청 거부"
    refused = dict(sent, forwarded=False, response="ERROR gateway closed")
    assert field.port_line(refused) == "진단 포트 · 닫힌 포트 접속 거부 (172.20.10.14)"
    assert field.port_line({"event": "request", "source": "x:1", "slot": "", "size": 0, "response": "ERROR bad"}) is None  # port probe, no payload
    assert field.port_line({"event": "something_else"}) is None


def test_a_slow_pi3_read_is_a_missing_sample_not_a_crash():
    import subprocess

    class Slow:
        def run(self, *args, **kwargs):
            raise subprocess.TimeoutExpired("ssh", 20)
    assert field.pi3_verdict(Slow()) is None


def test_the_run_has_no_two_things_called_watching():
    source = (field.ROOT / "bench" / "incident_field.py").read_text()
    assert source.count("watching = ") == 1 and "watching.set()" not in source  # the old `watching` is a dict, not an Event
