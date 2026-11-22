"""Real-board drill for the gateway incident response (laptop = attacker + control centre).

    laptop --7400--> Pi1 gateway --10.77.0.x ethernet--> Pi2 maintenance service (not on Wi-Fi)
    Pi1 central_incident: relays V3 to Pi2, closes the gate on recovery_failed, reports to the laptop,
    restores V2 over a restricted SSH key only after a human-approved signed order.

    python -m bench.incident_field setup    CONFIG        # idempotent board preparation
    python -m bench.incident_field run      CONFIG --output DIR [--verdict forced|real] [--decision human|approve|reject]
    python -m bench.incident_field teardown CONFIG

`--verdict forced` ends V3's trial with a scripted failure (the verdict is NOT a real CPU-contention
detection; result.json says forced_verdict: true). `--verdict real` is the Pi3 monitor + probation path.
CONFIG is a JSON file kept outside Git (hosts, key paths); see CONFIG_KEYS.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from capstone_ota.bundle import pack
from capstone_ota.debug_port import push
from capstone_ota.incident_server import IncidentServer
from dashboard.ivi.build import payload

ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = {"pi1_ssh", "pi2_ssh", "pi3_ssh", "laptop_ip", "release_dir"}
RUNTIME = "/home/otauser/capstone-incident"
IVI_ROOT = "/opt/capstone-ivi/device"
IVI_PUBLIC = "/opt/capstone-ivi/ivi-public.pem"
PI1_ETH, PI2_ETH = "10.77.0.1", "10.77.0.2"
MAINTENANCE_PORT, GATEWAY_PORT, SERVER_PORT = 7312, 7400, 8080
MARKER = "pi1-incident-repair"
PY = RUNTIME + "/venv/bin/python"
MONITOR_DIR = RUNTIME + "/monitor"
MONITOR_BASELINE = "/home/otauser/ivi-monitor/baseline.json"  # measured at 10 Hz with the cluster pinned to core 0 (2026-11-05, E4)
DEFAULT_PROFILE_HZ = 10
VULNERABLE_HOLD_SECONDS = 20   # how long the V1 screen stays up after a failed defence before the run ends (the recording stops then)
TRIAL_SECONDS = 120        # how long Pi3 must watch a new image before it may pass: the overrun has to be CONFIRMED well inside this
RECHECK_SECONDS = 20       # how long Pi2 watches a restored image after a rollback; the cockpit's "확인" gauge counts against it


def monitor_baseline_for(hz, cycle="synthetic"):
    """The monitor compares against a baseline measured at the same frame rate and on the same drive cycle
    (the needle moving more repaints more, which changes echo latency). bench.monitor_baseline makes one."""
    if hz == DEFAULT_PROFILE_HZ and cycle == "synthetic":
        return MONITOR_BASELINE
    suffix = "" if cycle == "synthetic" else f"-{cycle}"
    return f"/home/otauser/capstone-incident/monitor/baseline-{hz}hz{suffix}.json"


MAP_FLAG = "/run/capstone-ivi/map_active"
DROPIN = "/run/systemd/system/{unit}.d/field-cpu.conf"
PI2_ROOT_TO_PI3 = ["ssh", "-F", "/dev/null", "-i", "/root/.ssh/capstone-source-ed25519", "-o", "IdentitiesOnly=yes",
                   "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=yes",
                   "-o", "UserKnownHostsFile=/root/.ssh/capstone-verifier-known_hosts",
                   "-o", "HostKeyAlias=capstone-verifier"]


class Board:
    def __init__(self, ssh):
        if not isinstance(ssh, list) or not ssh or ssh[0] not in {"ssh", "/usr/bin/ssh"}:
            raise ValueError("a board must be an explicit ssh argument list")
        self.ssh = ssh

    def run(self, command, data=None, check=True, timeout=120):
        done = subprocess.run([*self.ssh, command], input=data, capture_output=True, text=True, timeout=timeout)
        if check and done.returncode:
            raise RuntimeError(f"remote exit {done.returncode}: {done.stderr[-800:]} {done.stdout[-400:]}")
        return done

    def write(self, path, text, mode="644"):
        self.run(f"umask 077; cat > {shlex.quote(path)} && chmod {mode} {shlex.quote(path)}", data=text)

    def put(self, path, data: bytes):
        done = subprocess.run([*self.ssh, f"mkdir -p {shlex.quote(str(Path(path).parent))} && cat > {shlex.quote(path)}"],
                              input=data, capture_output=True, timeout=120)
        if done.returncode:
            raise RuntimeError(f"upload failed: {done.stderr[-400:]!r}")


def load(path):
    config = json.loads(Path(path).read_text())
    if not CONFIG_KEYS <= config.keys():
        raise ValueError(f"config needs {sorted(CONFIG_KEYS)}")
    return config, {name: Board(config[f"{name}_ssh"]) for name in ("pi1", "pi2", "pi3")}


def gate_config(check_update, check_repair, recheck_update=None):
    update = {"public_key": IVI_PUBLIC, "check_command": check_update}
    if recheck_update is not None:
        update["recheck_command"] = recheck_update          # the old image's check after a rollback: shorter than the trial
    return {"checkout": RUNTIME, "python": RUNTIME + "/root-python", "root": IVI_ROOT, "central_host": PI1_ETH,
            "update": update, "repair": {"check_command": check_repair}}


def target_for(check, repair_check=None, recheck_update=None):
    """What Pi1's relay is told about Pi2. Must match gate_config(), which a test pins."""
    target = {"host": "pi2-incident", "checkout": RUNTIME, "python": RUNTIME + "/root-python", "root": IVI_ROOT,
              "public_key": IVI_PUBLIC, "check_command": check,
              "repair_check_command": repair_check or passing_recheck(), "application": "ivi"}
    if recheck_update is not None:
        target["recheck_command"] = recheck_update
    return target


def deploy(boards):
    """Install the current checkout as a wheel into each board's own venv (older environments are untouched)."""
    with tempfile.TemporaryDirectory(prefix="capstone-wheel-") as temporary:
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", temporary], check=True)
        wheel = next(Path(temporary).glob("capstone_ota-*.whl"))
        for name, board in boards.items():
            board.run(f"mkdir -p {RUNTIME} && [ -x {PY} ] || python3 -m venv {RUNTIME}/venv")
            board.put(f"{RUNTIME}/{wheel.name}", wheel.read_bytes())
            board.run(f"{PY} -m pip install -q --disable-pip-version-check --force-reinstall --no-deps {RUNTIME}/{wheel.name}"
                      f" && {PY} -c 'import capstone_ota.ota_gate, capstone_ota.central_incident'", timeout=300)
    return {name: "installed" for name in boards}


def forced_failure(seconds):
    """V3's trial result: wait (the attack lands meanwhile), then report a failure. Scripted, not measured."""
    return [PY, "-c", f"import json,time;time.sleep({seconds});print(json.dumps({{'passed': False, 'error': 'forced field verdict'}}))"]


def probation_command(pi3_address, probation=TRIAL_SECONDS, max_total=TRIAL_SECONDS * 4, trace="probation-trace.jsonl"):
    """The real verifier: Pi2's probation supervisor reads the Pi3 monitor's verdict over SSH.

    Pinned to cores 1-3 (the cluster owns core 0), as in the 2026-11-05 field run, so the measurement does
    not itself starve the cluster. The Device appends the slot path as the last argument."""
    ssh = [*PI2_ROOT_TO_PI3, f"otauser@{pi3_address}"]
    return ["taskset", "-c", "1-3", PY, "-m", "capstone_ota.probation", "--pi3-ssh", json.dumps(ssh),
            "--verdict-path", MONITOR_DIR + "/verdict.json", "--reset-path", MONITOR_DIR + "/monitor.reset",
            "--trace-file", f"{RUNTIME}/{trace}", "--probation", str(probation), "--safe", "5",
            "--max-wait", "300", "--max-total", str(max_total), "--poll", "2"]


# Demo only: the device's documented vulnerable mode. Every device the CLI builds skips the recovery tree-hash check.
VULNERABLE_PATCH = (
    "from capstone_ota.cluster import ClusterDevice as _D\n"
    "_init = _D.__init__\n"
    "def _weak(self, *a, **k):\n"
    "    k['recovery_verification'] = False\n"
    "    _init(self, *a, **k)\n"
    "_D.__init__ = _weak\n")


def vulnerable_launcher_script():
    """The root launcher for --vulnerable: same `-m capstone_ota ...` arguments, devices built without the recovery check."""
    return ("#!/bin/sh\nshift 2\nexec sudo -n %s -c %s \"$@\"\n" % (PY, shlex.quote(
        VULNERABLE_PATCH + "import sys, runpy\nsys.argv = ['capstone_ota'] + sys.argv[1:]\n"
        "runpy.run_module('capstone_ota', run_name='__main__')\n")))


def passing_recheck():
    return [PY, "-c", "import json;print(json.dumps({'passed': True}))"]


def setup(config, boards):
    pi1, pi2 = boards["pi1"], boards["pi2"]
    installed = deploy(boards)
    pi2.write(RUNTIME + "/root-python", f'#!/bin/sh\nexec sudo -n {PY} "$@"\n', "755")
    pi2.write(RUNTIME + "/gate.json", json.dumps(gate_config(passing_recheck(), passing_recheck())))
    pi1.run("[ -e ~/.ssh/incident_pi2_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -C " + MARKER + " -f ~/.ssh/incident_pi2_ed25519")
    public = pi1.run("cat ~/.ssh/incident_pi2_ed25519.pub").stdout.strip()
    host_key = " ".join(pi2.run("cat /etc/ssh/ssh_host_ed25519_key.pub").stdout.split()[:2])  # pinned from a trusted channel
    line = (f'from="{PI1_ETH}",restrict,command="{PY} -m capstone_ota.ota_gate --config {RUNTIME}/gate.json" {public}')
    pi2.run("cp -n ~/.ssh/authorized_keys ~/.ssh/authorized_keys.before-incident; "
            f"grep -v {shlex.quote(MARKER)} ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.new; "
            f"printf '%s\\n' {shlex.quote(line)} >> ~/.ssh/authorized_keys.new && mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys")
    pi1.write("/home/otauser/.ssh/known_hosts_incident", f"{PI2_ETH} {host_key}\n", "600")
    pi1.write("/home/otauser/.ssh/config", "Host pi2-incident\n    HostName " + PI2_ETH + "\n    User otauser\n"
              "    IdentityFile ~/.ssh/incident_pi2_ed25519\n    IdentitiesOnly yes\n"
              "    UserKnownHostsFile ~/.ssh/known_hosts_incident\n    StrictHostKeyChecking yes\n", "600")
    return {"deployed": installed, "pi1_key": public.split()[-1], "pi2_host_key_pinned": True}


def apply_demo_pace(pi2, config):
    """Demo pacing: slow the device's download and install stages so a viewer can see the bars fill (config key
    demo_pace {"download": s, "install": s, "park": s}). Without the key the file is removed and the stages run at full speed."""
    path = f"{IVI_ROOT}/demo-pace.json"
    pace = config.get("demo_pace")
    if pace:
        pi2.run(f"printf '%s' {shlex.quote(json.dumps(pace))} | sudo -n tee {path} >/dev/null")
    else:
        pi2.run(f"sudo -n rm -f {path}; true", check=False)


def wifi_address(board):
    """The board's Wi-Fi IPv4 address (the one the laptop and the other boards reach it on)."""
    return board.run("ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1").stdout.strip()


pi3_address = wifi_address


def make_order_keys(directory):
    """A fresh signing key pair for this run's control centre: private key (0600) and public key, as PEM files."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_path, public_path = Path(directory) / "order.key", Path(directory) / "order.pub"
    private = Ed25519PrivateKey.generate()
    private_path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
    private_path.chmod(0o600)
    public_path.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo))
    return private_path, public_path


def decide(host, incident, decision):
    """Click "approve" or "reject" for one incident on the control centre (what the person does in the browser)."""
    import http.client
    connection = http.client.HTTPConnection(host, SERVER_PORT, timeout=10)
    connection.request("POST", f"/incidents/{incident}/{'approve' if decision == 'approve' else 'reject'}")
    connection.getresponse().read()
    connection.close()


PROFILE_HOLD = RUNTIME + "/profile.hold"       # while this file exists on Pi1 the simulated vehicle stands still
HOLD_AFTER_STOPPED_S = 6.0                     # confirmed overrun and standing still this long: the rollback is imminent
HOLD_MAX_S = 300.0                             # the car is never held longer than this, whatever happens
RESTORED_STAGES = ("rolled_back", "committed")   # rollback AND the check after it are complete (both gauges 100%): drive on


class ProfileHold:
    """Keeps the simulated car standing still from the moment a rollback becomes certain until the cluster is back.

    The rollback and a refused recovery's repair take about a minute with the cluster off. If the drive profile kept
    going the car would be at 80 km/h when the cluster returns; held, it comes back at 0 and drives off from there.
    One hold per run: the monitor may keep saying "confirmed" for a while after the restore."""

    def __init__(self, set_hold, clear_hold, clock=time.time, max_s=HOLD_MAX_S):
        self._set, self._clear, self._clock, self._max_s = set_hold, clear_hold, clock, max_s
        self.holding, self.done, self._since = False, False, 0.0

    def step(self, verdict, stage):
        if self.done:
            return
        if not self.holding:
            if verdict and verdict.get("state") == "confirmed" and (verdict.get("bus_stopped_s") or 0) >= HOLD_AFTER_STOPPED_S:
                self._set()
                self.holding, self._since = True, self._clock()
            return
        if stage in RESTORED_STAGES or self._clock() - self._since > self._max_s:
            self.finish()

    def finish(self):
        if self.holding:
            self._clear()
            self.holding = False
        self.done = True


INSTALL_STOP_STAGES = ("archive_verify", "install")                       # download complete: the car must stand
BOOTED_STAGES = ("trial", "verify", "committed", "recover", "recheck", "rolled_back")   # the new (or restored) image runs
DRIVE_OFF_AFTER_S = 4.0     # the new image shows 0 km/h for a moment before the car accelerates


class InstallHold:
    """Keeps the simulated car standing still from the finished download until the new image has booted.

    An update is installed standing still: download complete -> the car brakes to 0 -> install, switch (cluster off),
    boot at 0 km/h -> a few seconds later the car accelerates. Stage-driven, once per run; the rollback hold
    (ProfileHold) is separate and may follow later."""

    def __init__(self, set_hold, clear_hold, clock=time.time, max_s=HOLD_MAX_S, drive_off_after_s=DRIVE_OFF_AFTER_S):
        self._set, self._clear, self._clock = set_hold, clear_hold, clock
        self._max_s, self._drive_off_after_s = max_s, drive_off_after_s
        self.holding, self.done, self._since, self._booted = False, False, 0.0, None

    def step(self, stage):
        if self.done:
            return
        if not self.holding:
            if stage in INSTALL_STOP_STAGES:
                self._set()
                self.holding, self._since = True, self._clock()
            return
        if stage in BOOTED_STAGES and self._booted is None:
            self._booted = self._clock()
        if (self._booted is not None and self._clock() - self._booted >= self._drive_off_after_s) \
                or self._clock() - self._since > self._max_s:
            self.finish()

    def finish(self):
        if self.holding:
            self._clear()
            self.holding = False
        self.done = True


def pi2_stage(pi2):
    """The device's current stage (display and pacing only), or None when it cannot be read."""
    try:
        done = pi2.run(f"sudo -n cat {IVI_ROOT}/progress.json", check=False, timeout=20)
        return json.loads(done.stdout).get("stage") if done.returncode == 0 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def run_profile_hold(boards, stop):
    """Thread body: stop the car for the install, and again for a rollback, by watching the device stage and the Pi3
    verdict; the hold file on Pi1 is what the drive profile obeys."""
    pi1, pi2, pi3 = boards["pi1"], boards["pi2"], boards["pi3"]
    set_hold = lambda: pi1.run(f"touch {PROFILE_HOLD}", check=False)      # noqa: E731
    clear_hold = lambda: pi1.run(f"rm -f {PROFILE_HOLD}", check=False)    # noqa: E731
    install, rollback = InstallHold(set_hold, clear_hold), ProfileHold(set_hold, clear_hold)
    try:
        while not stop.is_set():
            stage = pi2_stage(pi2)
            install.step(stage)
            if install.done:
                rollback.step(pi3_verdict(pi3), stage if rollback.holding else None)
            stop.wait(1.5)
    finally:
        install.finish()
        rollback.finish()


def start_real_services(boards, log, hz=DEFAULT_PROFILE_HZ, cycle="synthetic"):
    """Cluster pinned to core 0, IVI V2, Pi1 drive profile and the Pi3 monitor. Returns what was changed."""
    pi1, pi2, pi3 = boards["pi1"], boards["pi2"], boards["pi3"]
    for name in ("__init__.py", "drive_cycle.py", "play_profile.py"):
        pi1.put(f"{RUNTIME}/bench/{name}", (ROOT / "bench" / name).read_bytes())
    pi2.run("sudo systemctl stop capstone-ivi.service capstone-cluster.service; true", check=False)
    for unit, cpus in (("capstone-cluster.service", "0"), ("capstone-cluster-display.service", "1-3")):
        path = DROPIN.format(unit=unit)
        pi2.run(f"sudo mkdir -p {shlex.quote(path.rsplit('/', 1)[0])} && printf '[Service]\\nCPUAffinity={cpus}\\n' "
                f"| sudo tee {shlex.quote(path)} >/dev/null")
    pi2.run("sudo systemctl daemon-reload && sudo systemctl restart capstone-cluster-display.service "
            "&& sudo systemctl start capstone-cluster.service && sudo systemctl start capstone-ivi.service")
    # Kill in its own call: `pkill -f` would also match the shell whose command line holds the start command.
    pi3.run("pkill -f '[m]onitor.service'; true", check=False)
    pi3.run(f"mkdir -p {MONITOR_DIR} && rm -f {MONITOR_DIR}/monitor.reset; "
            f"nohup setsid {PY} -m capstone_ota.monitor.service --baseline {monitor_baseline_for(hz, cycle)} --out-dir {MONITOR_DIR} "
            f"> {MONITOR_DIR}/monitor.log 2>&1 < /dev/null & echo started")
    pi1.run("pkill -f '[p]lay_profile'; true", check=False)
    pi1.run(f"rm -f {PROFILE_HOLD}", check=False)                      # a stale hold would start the car standing still
    # No `cd ... &&` before the `&`: that backgrounds a subshell that keeps the ssh channel open forever.
    pi1.run(f"PYTHONPATH={RUNTIME} nohup setsid {PY} -m bench.play_profile --channel can0 --hz {hz} "
            f"--cycle {cycle} --hold-file {PROFILE_HOLD} > {RUNTIME}/profile.log 2>&1 < /dev/null & echo started")
    log("[services] cluster(core 0) + IVI V2 on Pi2, Pi3 monitor, Pi1 drive profile started")


def stop_real_services(boards):
    pi1, pi2, pi3 = boards["pi1"], boards["pi2"], boards["pi3"]
    pi1.run("pkill -f '[p]lay_profile'; true", check=False)
    pi3.run("pkill -f '[m]onitor.service'; true", check=False)
    pi2.run(f"rm -f {MAP_FLAG} 2>/dev/null; sudo systemctl stop capstone-ivi.service capstone-cluster.service; "
            + "; ".join(f"sudo rm -rf {shlex.quote(DROPIN.format(unit=u).rsplit('/', 1)[0])}"
                        for u in ("capstone-cluster.service", "capstone-cluster-display.service"))
            + "; sudo systemctl daemon-reload; sudo systemctl restart capstone-cluster-display.service; true", check=False)


SCREEN = RUNTIME + "/screen"
COCKPIT_FILES = ("Cockpit.qml", "Transitions.js", "fonts/SpaceGrotesk-Bold.ttf", "fonts/SpaceGrotesk-Medium.ttf",
                 "fonts/DMSans-Regular.ttf", "fonts/DMSans-Medium.ttf", "fonts/DMMono-Medium.ttf")


def version_labels(config):
    """Release version -> product name for the cockpit (read from the signed releases, plus the attack image)."""
    labels = {ATTACK_VERSION: "V1"}
    for name, label in (("v2", "V2"), ("v3", "V3")):
        meta = Path(config["release_dir"]) / "releases" / name / "release.json"
        labels[json.loads(meta.read_text())["version"]] = label
    return labels


def feeder_command(pi3_address, interval=0.5, ivi_ui=None, labels=None, recheck_s=0):
    """Collects what the cockpit shows (cluster capture, Pi3 verdict, update progress). Display only: it never decides."""
    ssh = [*PI2_ROOT_TO_PI3, f"otauser@{pi3_address}"]
    return ["taskset", "-c", "1-3", "/usr/bin/python3", "-m", "bench.cockpit_feeder",
            "--cluster-socket", "/run/capstone-cluster/model.sock", "--pi3-ssh", json.dumps(ssh),
            "--verdict-path", MONITOR_DIR + "/verdict.json",
            *(["--ivi-ui", str(ivi_ui)] if ivi_ui else []),
            *(["--version-labels", json.dumps(labels)] if labels else []), *(["--recheck-seconds", str(recheck_s)] if recheck_s else []),
            "--interval", str(interval)]


def map_flag_active(content):
    """Same rule as dashboard/ivi/ivi.py: the file exists (content is not None) and its text is not "0"."""
    return content is not None and content.strip() != "0"


def wait_for_map_click(read_flag, timeout=900, interval=1.0):
    """Poll the flag content (None = missing) until the user's tap on the map tile makes it active."""
    if not wait_for(lambda: map_flag_active(read_flag()), timeout, interval):
        raise SystemExit(f"nobody tapped the map (navigation) tile within {timeout:.0f} s; {MAP_FLAG} never became active")
    return True


VDISPLAY = ":94"


def virtual_display_command(display=VDISPLAY):
    """A private virtual X display for the cockpit when no monitor is attached (no network listener, local socket only)."""
    return ["/usr/bin/Xvfb", display, "-screen", "0", "2160x1440x24", "-ac", "-nolisten", "tcp"]


def fit_to_virtual_display(qml):
    """No window manager on the virtual display, so a fullscreen request is ignored and the window stays tiny: ask for the size."""
    if "visibility: Window.FullScreen;" not in qml:
        raise ValueError("Cockpit.qml no longer has the fullscreen request this patch expects")
    return qml.replace("visibility: Window.FullScreen;", "width: 2160; height: 1440;", 1)


def cockpit_ui_command(display=None):
    """The whole monitoring screen: fullscreen on the HDMI monitor's own desktop session, or on a private virtual display."""
    env = (f"QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software DISPLAY={display}" if display
           else "QT_QPA_PLATFORM=wayland XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0")
    return (f"cd {SCREEN}/cockpit 2>/dev/null; env {env} "
            f"QML_XHR_ALLOW_FILE_READ=1 QML_XHR_ALLOW_FILE_WRITE=1 nohup setsid taskset -c 1-3 qmlscene {SCREEN}/cockpit/Cockpit.qml "
            f"> {SCREEN}/ui.log 2>&1 < /dev/null & echo started")


def start_screen(boards, pi3_ip, log, interval=0.1, ivi_ui=None, virtual=False, labels=None):
    pi2 = boards["pi2"]
    for name in ("__init__.py", "cockpit_feeder.py"):
        pi2.put(f"{SCREEN}/bench/{name}", (ROOT / "bench" / name).read_bytes())
    for name in COCKPIT_FILES:
        pi2.put(f"{SCREEN}/cockpit/{name}", (ROOT / "dashboard/cockpit" / name).read_bytes())
    site = f"{RUNTIME}/venv/lib/python3.11/site-packages"
    pi2.run("sudo systemctl stop field-cockpit-feeder.service 2>/dev/null; sudo rm -f /run/cockpit/extra.jsonl; true", check=False)
    pi2.run("sudo systemd-run --quiet --collect --unit field-cockpit-feeder "
            f"-p WorkingDirectory={SCREEN} -p Environment=PYTHONPATH={SCREEN}:{site} "
            "-p Environment=XAUTHORITY=/var/lib/capstone-app/Xauthority " + shlex.join(feeder_command(pi3_ip, interval, ivi_ui, labels, RECHECK_SECONDS)))
    pi2.run("pkill -f '[q]mlscene.*Cockpit.qml'; true", check=False)
    if virtual:
        pi2.put(f"{SCREEN}/cockpit/Cockpit.qml",
                fit_to_virtual_display((ROOT / "dashboard/cockpit/Cockpit.qml").read_text()).encode())
        pi2.put(f"{SCREEN}/xclick.py", (ROOT / "bench" / "xclick.py").read_bytes())
        pi2.run("sudo systemctl stop field-cockpit-display.service 2>/dev/null; true", check=False)
        pi2.run("sudo systemd-run --quiet --collect --unit field-cockpit-display -p CPUAffinity=1-3 "
                + shlex.join(virtual_display_command()))
        time.sleep(2)
        pi2.run(cockpit_ui_command(VDISPLAY))
        log(f"[screen] cockpit feeder + monitoring screen started on Pi2's virtual display {VDISPLAY} (no monitor attached)")
    else:
        pi2.run(cockpit_ui_command())
        log("[screen] cockpit feeder + fullscreen monitoring screen started on Pi2's HDMI monitor")


INCIDENT_LINES = {   # what the vehicle says while the control centre decides on the restore
    "incident_reported": "서버에 사고 보고 전송 · 서버 승인 대기 중",
    "incident_approved": "서버 승인됨 · 복원 진행",
    "incident_rejected": "서버 거절됨 · 정비 필요",
    "incident_timeout": "서버 응답 없음 (시간 초과) · 정비 필요",
    "incident_unreachable": "서버에 연결할 수 없음 · 정비 필요",
}


def port_line(entry):
    """One short cockpit line for a gateway log entry, or None for noise (a bare port probe has no payload)."""
    if entry.get("event") == "gateway_closed":
        return "디버깅 포트 닫힘"
    wait = INCIDENT_LINES.get(entry.get("event"))
    if wait:
        return wait
    if entry.get("event") != "request" or not entry.get("slot"):
        return None
    source = str(entry.get("source", "?")).rsplit(":", 1)[0]
    if "gateway closed" in str(entry.get("response")):
        return f"디버깅 포트 · 닫힌 포트 접속 거부 ({source})"
    if entry.get("response") == "OK":
        return f"디버깅 포트 · 슬롯 {entry['slot']} 이미지 수신 ({source})"
    return f"디버깅 포트 · 요청 거부 ({source})"


def gateway_final_line():
    return f"디버깅 포트 {GATEWAY_PORT} 닫힘 · 게이트웨이 종료"


def show_port(pi2, text):
    """Append one line to the cockpit's log (the feeder reads it; display only)."""
    row = json.dumps({"text": text}, ensure_ascii=False)  # the feeder stamps Pi2's own clock, not the laptop's
    pi2.run("sudo mkdir -p /run/cockpit && sudo tee -a /run/cockpit/extra.jsonl >/dev/null", data=row + "\n", check=False, timeout=20)


def watch_gateway(pi1, pi2, stop):
    """Relay what Pi1's real gateway log says to the Pi2 cockpit, once per entry."""
    seen = 0
    while not stop.is_set():
        try:
            lines = pi1.run(f"cat {RUNTIME}/gateway.jsonl 2>/dev/null", check=False, timeout=15).stdout.splitlines()
            for line in lines[seen:]:
                text = port_line(json.loads(line))
                if text:
                    show_port(pi2, text)
            seen = len(lines)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        stop.wait(1.0)


def click_pi2(boards, x, y, button=1):
    """A real pointer click on the cockpit's virtual display (what a mouse would send)."""
    done = boards["pi2"].run(f"python3 {SCREEN}/xclick.py {VDISPLAY} {int(x)} {int(y)} {int(button)}", check=False, timeout=30)
    if done.returncode:
        raise RuntimeError("click failed: " + (done.stderr or done.stdout)[-300:])


def stop_screen(boards):
    pi2 = boards["pi2"]
    pi2.run("sudo systemctl stop field-cockpit-display.service 2>/dev/null; true", check=False)
    pi2.run("sudo systemctl stop field-cockpit-feeder.service 2>/dev/null; true", check=False)
    pi2.run("pkill -f '[q]mlscene.*Cockpit.qml'; true", check=False)


def pi3_verdict(pi3):
    try:
        done = pi3.run(f"cat {MONITOR_DIR}/verdict.json", check=False, timeout=20)
        return json.loads(done.stdout)
    except (ValueError, subprocess.SubprocessError):  # a slow ssh is one missing sample, not the end of the run
        return None


def healthy(verdict):
    """A fresh monitor verdict that has seen both frames of the cluster path (not blind, pairs measured)."""
    # The monitor's states: observing (healthy so far), suspect, confirmed, blind (cannot see the bus).
    return bool(verdict and verdict.get("frames", 0) > 0 and verdict.get("state") == "observing"
                and abs(time.time() - verdict.get("now", 0)) < 5 and verdict.get("recent_delay_ms") is not None)


def teardown(config, boards):
    boards["pi2"].run("sudo systemctl stop field-maintenance.service 2>/dev/null; "
                      f"grep -v {shlex.quote(MARKER)} ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.new && "
                      "mv ~/.ssh/authorized_keys.new ~/.ssh/authorized_keys; true", check=False)
    boards["pi1"].run("pkill -f '[c]apstone_ota.central_incident'; true", check=False)
    stop_real_services(boards)
    stop_screen(boards)  # the screen stays up after a run so the result can be read; teardown ends it
    return {"authorized_key_removed": True, "real_services_stopped": True}


def reachable(host, port, timeout=2.0):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for(predicate, timeout, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def recovery_slot(baseline):
    """The slot a rollback returns to is the one holding the committed release (the slots swap with every update, so a fixed letter misses)."""
    slot = baseline.get("active_slot")
    if slot not in ("A", "B"):
        raise SystemExit("cannot tell which slot holds the committed release: " + json.dumps(baseline))
    return slot


def integrity_command():
    """The device's own start-up recovery check (no URL, so nothing is installed). `status` alone never checks the slot tree."""
    return ["cd", RUNTIME, "&&", "./root-python", "-m", "capstone_ota", "--root", IVI_ROOT, "ivi-update",
            "--check-command", shlex.quote(json.dumps(passing_recheck()))]


def check_committed_slot(pi2):
    done = pi2.run(" ".join(integrity_command()), check=False, timeout=120)
    try:  # the CLI exits 2 for result=rolled_back (a valid stable state), so judge the answer, not the exit code
        healthy_answer = json.loads(done.stdout).get("phase") == "stable"
    except ValueError:
        healthy_answer = False
    if "OTA error" in done.stderr or not healthy_answer:
        raise SystemExit("Pi2's committed slot fails its recorded tree hash, so the device refuses updates (a previous run left it "
                         "tampered). Restore it before running: " + (done.stderr or done.stdout)[-300:].strip())


def restore_committed_slot(config, pi2, log=print):
    """Put the committed release's exact files back into the active slot (after a --vulnerable demo left the attacker's image).

    Uses the device's own extractor (a plain `tar -x` gives other file modes and so another tree hash), into a fresh
    folder, then runs the device's own recovery check to prove the tree matches its record again."""
    status = pi2_status(pi2)
    label = "v2"  # the baseline every drill starts from; a committed V3 is not restorable by this helper
    meta = json.loads((Path(config["release_dir"]) / "releases" / label / "release.json").read_text())
    if status["version"] != meta["version"]:
        raise SystemExit(f"Pi2 is at {status['version']}, this helper restores {meta['version']} only")
    archive = Path(config["release_dir"]) / "releases" / label / "app.tar.gz"
    remote_archive = f"{RUNTIME}/restore-{label}.tar.gz"
    pi2.put(remote_archive, archive.read_bytes())
    slot = f"{IVI_ROOT}/slots/{status['active_slot']}"
    code = ("import shutil, sys\nfrom pathlib import Path\nfrom capstone_ota.bundle import extract\n"
            f"slot = Path({slot!r})\nshutil.rmtree(slot)\nextract(Path({remote_archive!r}), slot)\nprint('restored', slot)\n")
    done = pi2.run(f"sudo -n {PY} -", data=code)
    log("[restore] " + done.stdout.strip())
    check_committed_slot(pi2)
    log("[restore] slot matches its recorded tree hash")


def pi2_status(pi2):
    # `status` exits 2 for a rolled_back device (a valid, stable state), so read the answer, not the exit code.
    done = pi2.run(f"cd {RUNTIME} && ./root-python -m capstone_ota --root {IVI_ROOT} status", check=False)
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise RuntimeError(f"cannot read Pi2 status: {done.stderr[-300:]}") from exc


DEFAULT_BASELINE = "2.0.0"
ATTACK_VERSION = "1.0.0"


def baseline_version(config):
    """The IVI version Pi2 must be committed at (config key "baseline_version", default unchanged)."""
    return str(config.get("baseline_version", DEFAULT_BASELINE))


def release_labels(config):
    """Incident server: committed version -> release directory that repairs it."""
    return {baseline_version(config): "v2"}


def attack_version(baseline):
    """The planted image must be an OLDER version than the baseline, or the device would not treat it as a downgrade."""
    from capstone_ota.bundle import version_key
    if version_key(ATTACK_VERSION) >= version_key(baseline):
        raise SystemExit(f"the attack image {ATTACK_VERSION} must be older than the baseline {baseline}")
    return ATTACK_VERSION


def attack_payload(staging):
    """The planted image is the real V1 build (radio screen), i.e. what would run if the rollback defence failed."""
    return payload(staging, "V1")


def require_baseline(status, config):
    wanted = baseline_version(config)
    if status["version"] != wanted or status["phase"] != "stable":
        raise SystemExit(f"Pi2 IVI is not at a stable baseline {wanted}: " + json.dumps(status))


def run(config, boards, output, verdict="forced", decision="human", attack=True, log=print, screen=False,
        map_trigger="script", virtual_screen=False, vulnerable=False):
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    pi1, pi2 = boards["pi1"], boards["pi2"]
    releases = Path(config["release_dir"])
    laptop = config["laptop_ip"]
    steps = []

    def note(step, **fields):
        steps.append({"step": step, "time": time.time(), **fields})
        log(f"[{step}] " + " ".join(f"{k}={v}" for k, v in fields.items() if k != "detail"))

    baseline = pi2_status(pi2)
    require_baseline(baseline, config)
    attack_as = attack_version(baseline_version(config))
    check_committed_slot(pi2)
    attack_slot = recovery_slot(baseline)
    note("baseline", status=baseline, attack_slot=attack_slot)
    if vulnerable:
        if not (attack and verdict == "real" and screen):
            raise SystemExit("--vulnerable needs the attack, --verdict real and --screen (it shows what the defence failure looks like)")
        pi2.write(RUNTIME + "/root-python", vulnerable_launcher_script(), "755")
        note("vulnerable_device", recovery_verification=False,
             warning="DEMO ONLY: this device skips the recovery tree-hash check; Pi2's slot is left unrestored by design")

    # Attack image: an older, validly signed IVI tree (the attacker already owns it).
    key = releases / "keys/ivi-private.pem"
    staging = output / "attack-payload"
    attack_payload(staging)
    pack(staging, output / "attack/v1", attack_as, 1, key)
    archive = output / "attack/v1/app.tar.gz"

    # Control centre on the laptop; Pi1 reports to it over Wi-Fi.
    order_key, order_pub = make_order_keys(output)
    server = IncidentServer((laptop, SERVER_PORT), releases / "releases", output / "incidents.json", order_key,
                            release_labels(config), order_lifetime=600.0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server_url = f"http://{laptop}:{SERVER_PORT}"
    note("control_centre", url=server_url)

    gateway_stop = threading.Event()
    hold_stop = threading.Event()        # ends the thread that holds the simulated car still during a rollback
    result = {"verdict_mode": verdict, "forced_verdict": verdict == "forced", "steps": steps}
    try:
        # Pi2: maintenance service on the Pi1 link only; gate pinned for exactly this run.
        pi2.run("sudo systemctl stop field-maintenance.service 2>/dev/null; true", check=False)
        pi2.run(f"sudo systemd-run --quiet --collect --unit field-maintenance {PY} -m capstone_ota.debug_port "
                f"--device-root {IVI_ROOT} --bind {PI2_ETH} --port {MAINTENANCE_PORT}")
        if verdict == "forced":
            trial, recheck = forced_failure(25), passing_recheck()
        else:
            start_real_services(boards, log, hz=int(config.get("profile_hz", DEFAULT_PROFILE_HZ)),
                                cycle=config.get("profile_cycle", "synthetic"))
            apply_demo_pace(pi2, config)
            threading.Thread(target=run_profile_hold, args=(boards, hold_stop), daemon=True).start()
            pi3_ip = pi3_address(boards["pi3"])
            if screen:
                start_screen(boards, pi3_ip, log, virtual=virtual_screen, labels=version_labels(config))
                show_port(pi2, f"정비 포트 {MAINTENANCE_PORT} 열림 · 이더넷 전용")
            trial = probation_command(pi3_ip)
            recheck = probation_command(pi3_ip, probation=RECHECK_SECONDS, max_total=120, trace="recheck-trace.jsonl")
            seen = wait_for(lambda: healthy(pi3_verdict(boards["pi3"])), 90, 3.0)
            note("monitor_health", healthy=bool(seen), verdict=pi3_verdict(boards["pi3"]))
            if not seen:
                raise SystemExit("the Pi3 monitor does not see both the vehicle input and the cluster answer")
        pi2.write(RUNTIME + "/gate.json", json.dumps(gate_config(trial, recheck, recheck_update=recheck)))

        # Pi1: target, keys, and the one process that runs gateway + relay + responder.
        pi1.write(RUNTIME + "/ivi-public.pem", (releases / "keys/ivi-public.pem").read_text())
        pi1.write(RUNTIME + "/order-public.pem", order_pub.read_text())
        pi1.write(RUNTIME + "/target.json", json.dumps(target_for(trial, recheck, recheck_update=recheck)))
        pi1.run(f"rm -f {RUNTIME}/result.json {RUNTIME}/gateway.jsonl {RUNTIME}/central.log")
        command = shlex.join([
            PY, "-m", "capstone_ota.central_incident", "--server", server_url, "--vehicle", "PI2-001",
            "--target", RUNTIME + "/target.json", "--release-url", server_url + "/releases/v3",
            "--release-public-key", RUNTIME + "/ivi-public.pem", "--order-public-key", RUNTIME + "/order-public.pem",
            "--listen", f"0.0.0.0:{GATEWAY_PORT}", "--upstream", f"{PI2_ETH}:{MAINTENANCE_PORT}",
            "--gateway-log", RUNTIME + "/gateway.jsonl", "--result-out", RUNTIME + "/result.json",
            "--bind", PI1_ETH, "--advertise", PI1_ETH, "--decision-timeout", "900", "--linger", "60"])
        pi1.run(f"nohup setsid {command} > {RUNTIME}/central.log 2>&1 < /dev/null & echo started")
        pi1_wifi = wifi_address(pi1)
        if not wait_for(lambda: reachable(pi1_wifi, GATEWAY_PORT), 30):
            raise SystemExit("Pi1 gateway did not come up: " + pi1.run(f"tail -5 {RUNTIME}/central.log", check=False).stdout)
        pi2_wifi = wifi_address(pi2)
        note("exposure", gateway_reachable_from_laptop=reachable(pi1_wifi, GATEWAY_PORT),
             pi2_maintenance_reachable_from_laptop=reachable(pi2_wifi, MAINTENANCE_PORT),
             pi2_maintenance_listening_on=pi2.run(f"ss -ltn | grep ':{MAINTENANCE_PORT} ' || true").stdout.split()[3:4])

        if screen:
            show_port(pi2, f"디버깅 포트 {GATEWAY_PORT} 열림 · Wi-Fi에서 접속 가능")
            threading.Thread(target=watch_gateway, args=(pi1, pi2, gateway_stop), daemon=True).start()

        # V3's trial is running on Pi2 (relayed by Pi1); wait for it, then attack through the gateway.
        in_trial = wait_for(lambda: '"stage": "verify"' in pi2.run(f"sudo cat {IVI_ROOT}/progress.json", check=False).stdout, 120)
        note("v3_in_trial", seen=bool(in_trial))
        timeline, watching = [], {"on": True}

        def watch():
            while watching["on"]:
                v = pi3_verdict(boards["pi3"]) if verdict == "real" else None
                if v:
                    timeline.append({"t": time.time(), **{k: v.get(k) for k in (
                        "state", "cls", "fired", "confirm_t", "monitored_s", "recent_delay_ms", "bus_speed", "bus_stopped_s")}})
                time.sleep(2.0)
        threading.Thread(target=watch, daemon=True).start()
        if attack:
            result["attack_slot"] = attack_slot
            result["attack_response"] = push(pi1_wifi, GATEWAY_PORT, attack_slot, archive)
            note("attack_through_gateway", response=result["attack_response"])
        if verdict == "real":
            if map_trigger == "script":
                time.sleep(8)
                pi2.run(f"touch {MAP_FLAG}")  # the map screen opens: V3's background workers start
                note("map_opened", flag=MAP_FLAG, by="script")
            else:
                note("waiting_for_map_click", message="Tap the navigation (map) tile on Pi2's screen; "
                     "that tap opens the map and starts the CPU contention.")
                wait_for_map_click(lambda: (lambda r: r.stdout if r.returncode == 0 else None)(
                    pi2.run(f"cat {MAP_FLAG}", check=False)), 900, 1.0)
                note("map_opened", flag=MAP_FLAG, by="ui")
        if vulnerable:
            # The defence is off: the rollback lands on the attacker's image, so there is no incident to wait for.
            settled = wait_for(lambda: (lambda s: s if s.get("phase") == "stable" else None)(pi2_status(pi2)), 600, 3.0)
            result["vulnerable_outcome"] = {"settled": bool(settled), "status": settled or pi2_status(pi2),
                                            "events": pi2.run(f"sudo grep -c VULNERABLE_MODE_RECOVERY_UNVERIFIED {IVI_ROOT}/events.jsonl 2>/dev/null; true",
                                                              check=False).stdout.strip()}
            note("vulnerable_outcome", settled=bool(settled))
            time.sleep(VULNERABLE_HOLD_SECONDS)  # keep the screen up: this is the picture the user sees if the defence fails
            note("final", status=pi2_status(pi2))   # the recorder stops here, before the boards are shut down
            watching["on"] = False
            result["verdict_timeline"] = timeline
            return result
        incident = wait_for(lambda: server.incidents, 240 if verdict == "forced" else 600) if attack else None
        if attack and not incident:
            raise SystemExit("no incident reached the control centre")
        if attack:
            note("incident_reported", id=incident[0]["id"], status=incident[0]["status"])
        if attack and decision == "human":
            note("waiting_for_human", url=server_url + "/")
        elif attack:
            decide(laptop, incident[0]["id"], decision)
            note("auto_decision", decision=decision)
        done = wait_for(lambda: pi1.run(f"cat {RUNTIME}/result.json 2>/dev/null", check=False).stdout.strip(), 1200, 2.0)
        if not done:
            raise SystemExit("Pi1 produced no result: " + pi1.run(f"tail -8 {RUNTIME}/central.log", check=False).stdout)
        result["pi1"] = json.loads(done)
        watching["on"] = False
        result["verdict_timeline"] = timeline
        if attack:
            result["reattack_response"] = push(pi1_wifi, GATEWAY_PORT, attack_slot, archive)
            note("reattack_through_closed_gate", response=result["reattack_response"])
        result["gateway_log"] = [json.loads(line) for line in pi1.run(f"cat {RUNTIME}/gateway.jsonl").stdout.splitlines()]
        result["pi2_final"] = pi2_status(pi2)
        result["incident_record"] = server.incidents
        note("final", status=result["pi2_final"])
    finally:
        if vulnerable:
            pi2.write(RUNTIME + "/root-python", f'#!/bin/sh\nexec sudo -n {PY} "$@"\n', "755")
        server.shutdown()
        server.server_close()
        pi2.run("sudo systemctl stop field-maintenance.service 2>/dev/null; true", check=False)
        if screen:
            time.sleep(2.5)  # let the watcher pass on the last gateway entries (the refused re-attack) first
        pi1.run("pkill -f '[c]apstone_ota.central_incident'; true", check=False)
        gateway_stop.set()
        hold_stop.set()                  # the thread frees the car on its way out
        if screen and verdict == "real":
            show_port(pi2, gateway_final_line())
            show_port(pi2, f"정비 포트 {MAINTENANCE_PORT} 닫힘")
        if verdict == "real":
            subprocess.run([*boards["pi3"].ssh, f"[ -d {MONITOR_DIR} ] && tar -C {MONITOR_DIR} -cf - evidence verdict.json monitor.log"],
                           stdout=(output / "pi3-evidence.tar").open("wb"), stderr=subprocess.DEVNULL, check=False, timeout=60)
            stop_real_services(boards)
        (output / "result.json").write_text(json.dumps(result, indent=1, default=str))
    return result


def main(argv=None) -> int:
    sys.stdout.reconfigure(line_buffering=True)  # helpers (the map clicker) watch this log; block buffering hid "waiting_for_map_click"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "run", "teardown"])
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verdict", choices=["forced", "real"], default="forced")
    parser.add_argument("--decision", choices=["human", "approve", "reject"], default="human")
    parser.add_argument("--no-attack", action="store_true", help="only watch the real detection and the normal rollback")
    parser.add_argument("--screen", action="store_true", help="show the whole cockpit on Pi2's HDMI monitor (needs --verdict real)")
    parser.add_argument("--virtual-screen", action="store_true", help="no monitor: run the cockpit on a private virtual display on Pi2 (implies --screen)")
    parser.add_argument("--map-trigger", choices=["script", "ui"], default="script",
                        help="script: touch the flag after the attack; ui: wait for the tap on the map tile of the cockpit screen (use with --screen)")
    parser.add_argument("--vulnerable", action="store_true",
                        help="DEMO ONLY: Pi2 skips the recovery check, so the attack wins and the V1 screen shows. Needs a manual slot restore afterwards")
    args = parser.parse_args(argv)
    args.screen = args.screen or args.virtual_screen
    if args.screen and args.verdict != "real":
        parser.error("--screen needs --verdict real")
    config, boards = load(args.config)
    if args.command == "setup":
        print(json.dumps(setup(config, boards)))
    elif args.command == "teardown":
        print(json.dumps(teardown(config, boards)))
    else:
        if args.output is None:
            parser.error("run needs --output")
        outcome = run(config, boards, args.output, args.verdict, args.decision, attack=not args.no_attack, screen=args.screen, virtual_screen=args.virtual_screen,
                       map_trigger=args.map_trigger, vulnerable=args.vulnerable)
        print(json.dumps({"final": outcome.get("pi2_final"), "forced_verdict": outcome["forced_verdict"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
