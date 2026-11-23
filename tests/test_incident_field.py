"""bench.incident_field: what Pi1 sends must be exactly what Pi2's pinned gate allows (no board needed)."""
import shlex
import struct
import subprocess

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
    assert field.port_line(sent) == "디버깅 포트 · 슬롯 A 이미지 수신 (172.20.10.14)"
    assert field.port_line({"event": "gateway_closed", "reason": "device reported recovery_failed"}) == "디버깅 포트 닫힘"
    refused = dict(sent, forwarded=False, response="ERROR gateway closed")
    assert field.port_line(refused) == "디버깅 포트 · 닫힌 포트 접속 거부 (172.20.10.14)"
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
    assert field.gateway_final_line() == f"디버깅 포트 {field.GATEWAY_PORT} 닫힘 · 게이트웨이 종료"


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
    assert "--virtual-screen" in source and "start_screen(boards, pi3_ip, log, virtual=virtual_screen, labels=version_labels(config))" in source


def test_the_attack_targets_the_slot_that_holds_the_committed_release_not_a_fixed_letter():
    import pytest
    assert field.recovery_slot({"active_slot": "A", "phase": "stable"}) == "A"   # the slot a rollback would return to
    assert field.recovery_slot({"active_slot": "B", "phase": "stable"}) == "B"
    with pytest.raises(SystemExit):
        field.recovery_slot({"active_slot": None, "phase": "stable"})
    source = (field.ROOT / "bench" / "incident_field.py").read_text()
    assert 'GATEWAY_PORT, "A"' not in source and 'GATEWAY_PORT, "B"' not in source   # both the attack and the re-attack use the chosen slot


def test_the_vulnerable_demo_device_skips_only_the_recovery_check(tmp_path):
    """--vulnerable swaps the root launcher for one that builds every device with recovery_verification=False."""
    import subprocess, sys
    script = field.vulnerable_launcher_script()
    assert script.startswith("#!/bin/sh\n") and field.PY in script and "sudo -n" in script
    done = subprocess.run([sys.executable, "-c", field.VULNERABLE_PATCH + "\nfrom capstone_ota.ivi import IviDevice\n"
                           "print(IviDevice(%r).recovery_verification)" % str(tmp_path)],
                          capture_output=True, text=True, cwd=str(field.ROOT))
    assert done.stdout.strip() == "False", done.stderr


def test_cockpit_shows_product_names_not_release_numbers(tmp_path):
    import json
    releases = tmp_path / "releases"
    for name, version in (("v2", "3.3.0"), ("v3", "3.4.0")):
        (releases / name).mkdir(parents=True)
        (releases / name / "release.json").write_text(json.dumps({"version": version}))
    labels = field.version_labels({"release_dir": str(tmp_path)})
    assert labels == {field.ATTACK_VERSION: "V1", "3.3.0": "V2", "3.4.0": "V3"}
    assert "--version-labels" in field.feeder_command("10.0.0.1", labels=labels)


class _RecordingBoard:
    def __init__(self):
        self.commands = []

    def run(self, command, **_kw):
        self.commands.append(command)
        return type("R", (), {"stdout": "", "returncode": 0})()

    def put(self, *_a, **_kw):
        pass


def test_profile_command_carries_the_selected_cycle():
    boards = {name: _RecordingBoard() for name in ("pi1", "pi2", "pi3")}
    field.start_real_services(boards, lambda *_: None, hz=50, cycle="demo")
    assert any("play_profile" in c and "--cycle demo" in c and "--hz 50" in c for c in boards["pi1"].commands)


def test_demo_pace_is_written_to_the_device_root_when_configured():
    pi2 = _RecordingBoard()
    field.apply_demo_pace(pi2, {"demo_pace": {"download": 10, "install": 12}})
    command = " ".join(pi2.commands)
    assert f"{field.IVI_ROOT}/demo-pace.json" in command and "sudo" in command
    assert '"download": 10' in command and '"install": 12' in command


def test_demo_pace_file_is_removed_when_not_configured():
    pi2 = _RecordingBoard()
    field.apply_demo_pace(pi2, {})
    assert pi2.commands and "rm -f" in pi2.commands[0] and f"{field.IVI_ROOT}/demo-pace.json" in pi2.commands[0]


def test_there_is_no_second_watch_after_a_rollback_and_so_no_gauge_for_it():
    assert not hasattr(field, "RECHECK_SECONDS")
    command = field.feeder_command("10.0.0.4")
    assert "--recheck-seconds" not in command and "--trial-seconds" not in command
    source = open(field.__file__, encoding="utf-8").read()
    assert "recheck = passing_recheck()" in source and 'trace="recheck-trace.jsonl"' not in source.split("def run(")[1]


def test_a_failed_defence_run_holds_the_v1_screen_briefly_and_then_announces_its_end():
    assert 10 <= field.VULNERABLE_HOLD_SECONDS <= 30        # a 75 s hold left a minute of nothing at the end of every video
    source = open(field.__file__, encoding="utf-8").read()
    held = source[source.index('note("vulnerable_outcome"'):]
    held = held[:held.index("watching[\"on\"] = False")]
    assert "time.sleep(VULNERABLE_HOLD_SECONDS)" in held and 'note("final"' in held   # the recorder stops on the final line


def test_the_trial_is_long_enough_for_the_monitor_to_confirm_an_overrun_before_it_can_pass():
    """On 2026-11-20 a 45 s trial passed with the overrun still unconfirmed and V3 was committed for good (min_svn 10).
    The map opens ~25 s into the trial and confirming needs three 10 s windows, so the trial must be far longer."""
    assert field.TRIAL_SECONDS >= 100
    command = field.probation_command("10.0.0.4")
    assert command[command.index("--probation") + 1] == str(field.TRIAL_SECONDS)
    assert float(command[command.index("--max-total") + 1]) >= field.TRIAL_SECONDS * 2


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _hold(**kw):
    calls = []
    clock = _Clock()
    hold = field.ProfileHold(lambda: calls.append("hold"), lambda: calls.append("release"), clock=clock, **kw)
    return hold, calls, clock


def _verdict(state, stopped):
    return {"state": state, "bus_stopped_s": stopped}


def test_the_car_is_held_once_the_overrun_is_confirmed_and_it_has_stood_still_a_while():
    hold, calls, _ = _hold()
    hold.step(_verdict("observing", 20), None)
    hold.step(_verdict("confirmed", 3), None)        # confirmed but still moving / just stopped: not yet
    assert calls == []
    hold.step(_verdict("confirmed", 7), None)
    assert calls == ["hold"] and hold.holding
    hold.step(_verdict("confirmed", 12), "recover")  # stays held through the whole rollback and a refused recovery
    hold.step(_verdict("confirmed", 40), "recovery_failed")
    assert calls == ["hold"]


def test_the_car_is_released_only_when_rollback_and_check_are_both_complete_and_never_held_again_in_that_run():
    hold, calls, _ = _hold()
    hold.step(_verdict("confirmed", 7), None)
    hold.step(_verdict("confirmed", 15), "recover")
    hold.step(_verdict("confirmed", 20), "recheck")  # the restored image runs but the 검증 gauge is still filling
    assert calls == ["hold"] and hold.holding
    hold.step(_verdict("confirmed", 40), "rolled_back")  # both gauges at 100%: drive on, from standstill
    assert calls == ["hold", "release"] and not hold.holding
    hold.step(_verdict("confirmed", 30), None)       # the monitor may still say confirmed for a while
    assert calls == ["hold", "release"]


def test_a_hold_never_lasts_longer_than_the_safety_limit():
    hold, calls, clock = _hold(max_s=100)
    hold.step(_verdict("confirmed", 7), None)
    clock.now += 101
    hold.step(_verdict("confirmed", 90), "recovery_failed")
    assert calls == ["hold", "release"]


def test_finishing_always_frees_the_car():
    hold, calls, _ = _hold()
    hold.step(_verdict("confirmed", 7), None)
    hold.finish()
    assert calls == ["hold", "release"]
    hold.finish()
    assert calls == ["hold", "release"]              # nothing to free twice


def test_the_profile_on_pi1_is_started_with_the_hold_file_and_a_stale_hold_is_cleared():
    boards = {name: _RecordingBoard() for name in ("pi1", "pi2", "pi3")}
    field.start_real_services(boards, lambda *_: None, hz=50, cycle="demo")
    commands = boards["pi1"].commands
    assert any("play_profile" in c and f"--hold-file {field.PROFILE_HOLD}" in c for c in commands)
    assert any(c.startswith(f"rm -f {field.PROFILE_HOLD}") for c in commands)


def _install_hold(**kw):
    calls = []
    clock = _Clock()
    hold = field.InstallHold(lambda: calls.append("hold"), lambda: calls.append("release"), clock=clock, **kw)
    return hold, calls, clock


def test_the_car_stops_when_the_download_is_done_and_stays_stopped_through_install_and_boot():
    hold, calls, clock = _install_hold()
    for stage in (None, "metadata", "signature_verified", "download"):
        hold.step(stage)
    assert calls == []                                   # still driving while the files come in
    hold.step("archive_verify")                          # download complete: the car must stand for the install
    assert calls == ["hold"] and hold.holding
    hold.step("install")                                 # installing, switching, cluster off: still standing
    hold.step("install")
    assert calls == ["hold"]


def test_the_car_drives_off_a_few_seconds_after_the_new_image_has_booted():
    hold, calls, clock = _install_hold(drive_off_after_s=4)
    hold.step("archive_verify")
    hold.step("install")
    hold.step("trial")                                   # the new image runs: it shows 0 km/h first ...
    assert calls == ["hold"]
    clock.now += 3
    hold.step("trial")
    assert calls == ["hold"]
    clock.now += 2
    hold.step("trial")                                   # ... and only then does the car accelerate
    assert calls == ["hold", "release"] and not hold.holding


def test_the_install_stop_happens_once_and_never_blocks_the_rollback_hold():
    hold, calls, clock = _install_hold(drive_off_after_s=0)
    hold.step("archive_verify")
    hold.step("trial")
    hold.step("trial")
    assert calls == ["hold", "release"]
    for stage in ("verify", "archive_verify", "install", "recheck"):    # later stages never stop the car again
        hold.step(stage)
    assert calls == ["hold", "release"]


def test_an_install_stop_never_lasts_longer_than_the_safety_limit_and_finish_frees_the_car():
    hold, calls, clock = _install_hold(max_s=100)
    hold.step("archive_verify")
    clock.now += 101
    hold.step("install")                                 # the install never reached the boot: free the car anyway
    assert calls == ["hold", "release"]
    other, other_calls, _ = _install_hold()
    other.step("archive_verify")
    other.finish()
    other.finish()
    assert other_calls == ["hold", "release"]


def test_the_gate_allows_an_update_whose_post_rollback_recheck_is_its_own_command_and_nothing_else():
    trial = field.probation_command("172.20.10.4")
    recheck = field.probation_command("172.20.10.4", probation=20, max_total=120, trace="recheck-trace.jsonl")
    config = field.gate_config(trial, recheck, recheck_update=recheck)
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial, recheck, recheck_update=recheck))), config) is not None
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial, recheck))), config) is None           # recheck dropped
    longer = field.probation_command("172.20.10.4", probation=120, max_total=480, trace="recheck-trace.jsonl")
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial, recheck, recheck_update=longer))), config) is None


def test_the_gate_still_allows_the_repair_when_the_update_has_its_own_recheck_command():
    """The repair command is derived from the update command; the update's --recheck-command must not leak into it
    (the gate refused the human-approved restore: 'restore failed: actor exited unsuccessfully')."""
    trial = field.probation_command("172.20.10.4")
    recheck = field.probation_command("172.20.10.4", probation=20, max_total=120, trace="recheck-trace.jsonl")
    config = field.gate_config(trial, recheck, recheck_update=recheck)
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = field.target_for(trial, recheck, recheck_update=recheck)
    command = responder._repair_command("http://" + field.PI1_ETH + ":4321")[-1]
    assert "--recheck-command" not in command
    assert ota_gate.allowed(command, config) is not None


def test_the_wait_for_the_control_centre_shows_on_the_cockpit():
    assert field.port_line({"event": "incident_reported", "id": "INC-0001"}) == "서버에 사고 보고 전송 · 서버 승인 대기 중"
    assert field.port_line({"event": "incident_approved"}) == "서버 승인됨 · 복원 진행"
    assert field.port_line({"event": "incident_rejected"}) == "서버 거절됨 · 정비 필요"
    assert field.port_line({"event": "incident_timeout"}) == "서버 응답 없음 (시간 초과) · 정비 필요"
    assert field.port_line({"event": "incident_unreachable"}) == "서버에 연결할 수 없음 · 정비 필요"


class _Board:
    def __init__(self, out):
        self.out, self.commands = out, []

    def run(self, command, **_):
        self.commands.append(command)
        return type("R", (), {"stdout": self.out})()


def test_every_board_asks_for_its_wifi_address_the_same_way():
    board = _Board("172.20.10.4\n")
    assert field.wifi_address(board) == "172.20.10.4"
    assert "wlan0" in board.commands[0]


def test_the_control_centre_key_pair_is_written_private_to_the_owner(tmp_path):
    from capstone_ota.order import verify_order  # noqa: F401  (the pair must be one the control centre can use)
    private, public = field.make_order_keys(tmp_path)
    assert private.stat().st_mode & 0o777 == 0o600
    assert b"BEGIN PRIVATE KEY" in private.read_bytes() and b"BEGIN PUBLIC KEY" in public.read_bytes()


def test_a_decision_is_posted_to_the_incident_of_the_control_centre(monkeypatch):
    seen = []

    class Conn:
        def __init__(self, host, port, timeout):
            seen.append((host, port))

        def request(self, method, path):
            seen.append((method, path))

        def getresponse(self):
            return type("R", (), {"read": lambda self: b""})()

        def close(self):
            pass

    import http.client
    monkeypatch.setattr(http.client, "HTTPConnection", Conn)
    field.decide("10.0.0.1", "INC-0001", "reject")
    field.decide("10.0.0.1", "INC-0002", "approve")
    assert seen[1] == ("POST", "/incidents/INC-0001/reject") and seen[3] == ("POST", "/incidents/INC-0002/approve")
    assert seen[0] == ("10.0.0.1", field.SERVER_PORT)


def test_an_install_that_was_never_seen_standing_does_not_block_the_rollback_hold():
    # The poll missed archive_verify/install (a ssh hiccup or a fast install) and first sees the running new image.
    hold, calls, _ = _install_hold()
    hold.step("trial")
    assert hold.done and calls == []                 # nothing to hold any more: the rollback hold may go on


def test_a_stale_finished_state_from_the_last_run_does_not_cancel_the_install_stop():
    hold, calls, _ = _install_hold()
    hold.step("committed")                           # progress.json of the previous run, before the update starts
    assert not hold.done
    hold.step("archive_verify")
    assert calls == ["hold"]


# ---- a real pointer click on the HDMI monitor (Pi2 has no mouse or touch panel) ----

def test_the_pointer_map_trigger_is_accepted(monkeypatch):
    seen = {}
    monkeypatch.setattr(field, "load", lambda path: ({}, {}))
    monkeypatch.setattr(field, "run", lambda *a, **k: seen.update(k) or {"forced_verdict": False})
    assert field.main(["run", "cfg.json", "--output", "out", "--verdict", "real", "--screen", "--map-trigger", "pointer"]) == 0
    assert seen["map_trigger"] == "pointer"


def test_uinput_click_arguments_are_checked_and_events_have_the_kernel_layout():
    from bench import uinput_click
    assert uinput_click.parse(["1050", "1387"]) == (1050, 1387)
    for bad in (["1050"], ["x", "1"], ["-1", "5"], ["2160", "5"], ["5", "1440"]):
        with pytest.raises(SystemExit):
            uinput_click.parse(bad)
    assert len(uinput_click.device_description()) == 1116        # struct uinput_user_dev
    assert len(uinput_click.event(uinput_click.EV_KEY, uinput_click.BTN_LEFT, 1)) == struct.calcsize("llHHi")


def test_pointer_click_sends_the_helper_and_runs_it_as_root_on_pi2():
    class Pi2:
        def __init__(self):
            self.puts, self.runs = [], []

        def put(self, path, data, mode="644"):
            self.puts.append((path, data))

        def run(self, command, **kwargs):
            self.runs.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="clicked 1050 1387\n", stderr="")

    pi2 = Pi2()
    field.pointer_click_pi2({"pi2": pi2}, 1050, 1387)
    assert pi2.puts[0][0].endswith("/uinput_click.py") and b"UI_DEV_CREATE" in pi2.puts[0][1]
    assert pi2.runs[-1].startswith("sudo -n python3 ") and pi2.runs[-1].endswith("uinput_click.py 1050 1387")
    failing = Pi2()
    failing.run = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="no uinput")
    with pytest.raises(RuntimeError, match="click failed"):
        field.pointer_click_pi2({"pi2": failing}, 1, 1)


def test_a_status_line_for_people_names_versions_v1_v2_v3(tmp_path):
    for name, version in (("v2", "3.13.0"), ("v3", "3.14.0")):
        (tmp_path / "releases" / name).mkdir(parents=True)
        (tmp_path / "releases" / name / "release.json").write_text('{"version": "%s"}' % version)
    config = {"release_dir": str(tmp_path)}
    status = {"version": "3.13.0", "trial_version": "3.14.0", "phase": "stable", "min_svn": 15, "result": "rolled_back"}
    assert field.shown_status(config, status) == dict(status, version="V2", trial_version="V3")
    assert field.shown_status(config, {"version": "7.7.7", "trial_version": None}) == {"version": "7.7.7", "trial_version": None}
    assert field.shown_status({"release_dir": str(tmp_path / "missing")}, status) == status   # no labels known: unchanged


def test_the_map_tap_delay_can_be_shortened_in_the_config():
    source = open(field.__file__, encoding="utf-8").read()
    assert 'config.get("map_lead", POINTER_LEAD_SECONDS)' in source


def test_no_port_closed_popup_after_the_control_centre_rejected_the_restore():
    approved = [{"event": "incident_reported"}, {"event": "incident_approved"}, {"event": "gateway_closed"}]
    rejected = [{"event": "incident_reported"}, {"event": "incident_rejected"}, {"event": "gateway_closed"}]
    assert field.relay_lines(approved, {"rejected": False})[-1] == "디버깅 포트 닫힘"          # the popup comes after a restore
    lines = field.relay_lines(rejected, {"rejected": False})
    assert lines == ["서버에 사고 보고 전송 · 서버 승인 대기 중", "서버 거절됨 · 정비 필요"]
    state = {"rejected": False}                    # the rejection and the closing may arrive in different polls
    assert field.relay_lines(rejected[:2], state)[-1] == "서버 거절됨 · 정비 필요"
    assert field.relay_lines(rejected[2:], state) == []
