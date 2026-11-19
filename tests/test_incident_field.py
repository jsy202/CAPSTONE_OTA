"""bench.incident_field: what Pi1 sends must be exactly what Pi2's pinned gate allows (no board needed)."""
import shlex

import pytest

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


def test_the_cockpit_process_may_write_the_map_flag_file():
    # Ivi.qml writes <run>/map_active by XMLHttpRequest PUT; Qt refuses that unless this variable is set.
    assert "QML_XHR_ALLOW_FILE_WRITE=1" in field.cockpit_ui_command()


def test_map_flag_rule_is_shared_by_the_app_and_the_orchestrator():
    assert field.map_flag_active(None) is False          # file missing (cat printed nothing / failed)
    assert field.map_flag_active("") is True             # old `touch`: an empty file still means open
    assert field.map_flag_active("1\n") is True
    assert field.map_flag_active("0\n") is False
    assert field.map_flag_active(" 0 ") is False


def test_map_trigger_option_defaults_to_the_script_and_accepts_ui(monkeypatch):
    seen = {}
    monkeypatch.setattr(field, "load", lambda path: ({}, {}))
    monkeypatch.setattr(field, "run", lambda *a, **k: seen.update(k) or {"forced_verdict": None})
    assert field.main(["run", "cfg.json", "--output", "out"]) == 0
    assert seen["map_trigger"] == "script"
    assert field.main(["run", "cfg.json", "--output", "out", "--map-trigger", "ui"]) == 0
    assert seen["map_trigger"] == "ui"
    with pytest.raises(SystemExit):
        field.main(["run", "cfg.json", "--output", "out", "--map-trigger", "never"])


def test_ui_map_trigger_waits_for_the_tap_and_times_out_clearly():
    reads = iter([None, "", ])
    waited = field.wait_for_map_click(lambda: next(reads), timeout=5, interval=0)
    assert waited is True
    with pytest.raises(SystemExit, match="map"):
        field.wait_for_map_click(lambda: "0", timeout=0.05, interval=0.01)


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


def test_the_cockpit_capture_interval_can_be_chosen_for_the_interference_measurement():
    assert field.feeder_command("172.20.10.4")[-2:] == ["--interval", "0.5"]          # default is unchanged
    assert field.feeder_command("172.20.10.4", interval=0.1)[-2:] == ["--interval", "0.1"]


def test_final_gateway_port_line_says_closed_not_just_ended():
    assert field.gateway_final_line() == f"진단 포트 {field.GATEWAY_PORT} 닫힘 · 게이트웨이 종료"


def test_the_screen_run_captures_the_cluster_ten_times_a_second_by_default():
    import inspect
    assert inspect.signature(field.start_screen).parameters["interval"].default == 0.1  # measured: no extra Pi3 delay (2026-11-19)


def test_the_baseline_checks_the_committed_slot_not_only_the_status_file():
    command = field.integrity_command()
    assert "ivi-update" in command and "--check-command" in command
    assert not any(part.startswith("http") for part in command)  # no URL: it only runs the device's own recovery check


def test_a_tampered_committed_slot_stops_the_run_before_anything_is_started():
    import subprocess
    import pytest

    class Tampered:
        def run(self, command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "OTA error: recovery application tree hash mismatch\n")
    with pytest.raises(SystemExit, match="tree hash"):
        field.check_committed_slot(Tampered())

    class Fine:
        def run(self, command, **kwargs):
            return subprocess.CompletedProcess(command, 0, '{"phase": "stable"}', "")
    field.check_committed_slot(Fine())


def test_a_rolled_back_but_healthy_device_passes_the_slot_check_despite_exit_code_2():
    import subprocess

    class RolledBack:  # the CLI exits 2 for result=rolled_back, which is a valid stable state
        def run(self, command, **kwargs):
            return subprocess.CompletedProcess(command, 2, '{"phase": "stable", "result": "rolled_back"}', "")
    field.check_committed_slot(RolledBack())


def test_the_screen_can_be_started_with_a_preview_ivi_ui_without_changing_the_default_command():
    plain = field.feeder_command("172.20.10.4", 0.1)
    assert "--ivi-ui" not in plain and plain[-2:] == ["--interval", "0.1"]
    preview = field.feeder_command("172.20.10.4", 0.1, ivi_ui="/home/otauser/x/ui")
    assert preview[preview.index("--ivi-ui") + 1] == "/home/otauser/x/ui" and preview[-2:] == ["--interval", "0.1"]


def test_the_cockpit_can_run_on_a_private_virtual_display_when_no_monitor_is_attached():
    default = field.cockpit_ui_command()
    assert "QT_QPA_PLATFORM=wayland" in default and " DISPLAY=" not in default      # unchanged: the HDMI monitor's desktop
    virtual = field.cockpit_ui_command(display=":94")
    assert "DISPLAY=:94" in virtual and "QT_QPA_PLATFORM=xcb" in virtual and "wayland" not in virtual.lower()
    assert "QML_XHR_ALLOW_FILE_WRITE=1" in virtual and "taskset -c 1-3" in virtual   # the map tap must still be able to write the flag
    assert virtual.rstrip().endswith("& echo started") and "< /dev/null" in virtual


def test_the_virtual_display_is_a_private_xvfb_without_a_network_listener():
    command = field.virtual_display_command(":94")
    assert command[:2] == ["/usr/bin/Xvfb", ":94"] and "-nolisten" in command and "tcp" in command
    assert command[command.index("-screen") + 2] == "2160x1440x24"


def test_the_click_tool_parses_its_arguments_and_rejects_nonsense():
    import pytest
    from bench import xclick
    assert xclick.parse([":94", "1050", "1160"]) == (":94", 1050, 1160, 1)
    assert xclick.parse([":94", "10", "20", "3"]) == (":94", 10, 20, 3)
    with pytest.raises(SystemExit):
        xclick.parse([":94", "x", "20"])
    with pytest.raises(SystemExit):
        xclick.parse([":94", "-5", "20"])
    with pytest.raises(SystemExit):
        xclick.parse(["../etc", "5", "5"])


def test_on_the_virtual_display_the_cockpit_window_gets_an_explicit_size_not_a_fullscreen_request():
    source = (field.ROOT / "dashboard/cockpit/Cockpit.qml").read_text()
    assert "visibility: Window.FullScreen" in source        # the shipped file stays a fullscreen window for a real monitor
    fitted = field.fit_to_virtual_display(source)
    assert "visibility: Window.FullScreen" not in fitted and "width: 2160; height: 1440" in fitted
    assert fitted.count("visible: true") == source.count("visible: true")   # only the size request changed
    with __import__("pytest").raises(ValueError):
        field.fit_to_virtual_display("Window { visible: true }")             # a changed cockpit must not be silently mis-patched


def test_the_run_can_use_the_virtual_screen_and_the_cli_flag_implies_the_screen():
    import inspect
    assert inspect.signature(field.run).parameters["virtual_screen"].default is False
    source = (field.ROOT / "bench" / "incident_field.py").read_text()
    assert "--virtual-screen" in source and "start_screen(boards, pi3_ip, log, virtual=virtual_screen)" in source
