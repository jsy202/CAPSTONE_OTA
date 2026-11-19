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
MONITOR_BASELINE = "/home/otauser/ivi-monitor/baseline.json"  # measured with the cluster pinned to core 0 (2026-11-05, E4)
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


def gate_config(check_update, check_repair):
    return {"checkout": RUNTIME, "python": RUNTIME + "/root-python", "root": IVI_ROOT, "central_host": PI1_ETH,
            "update": {"public_key": IVI_PUBLIC, "check_command": check_update},
            "repair": {"check_command": check_repair}}


def target_for(check, repair_check=None):
    """What Pi1's relay is told about Pi2. Must match gate_config(), which a test pins."""
    return {"host": "pi2-incident", "checkout": RUNTIME, "python": RUNTIME + "/root-python", "root": IVI_ROOT,
            "public_key": IVI_PUBLIC, "check_command": check,
            "repair_check_command": repair_check or passing_recheck(), "application": "ivi"}


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


def probation_command(pi3_address, probation=45, max_total=400, trace="probation-trace.jsonl"):
    """The real verifier: Pi2's probation supervisor reads the Pi3 monitor's verdict over SSH.

    Pinned to cores 1-3 (the cluster owns core 0), as in the 2026-11-05 field run, so the measurement does
    not itself starve the cluster. The Device appends the slot path as the last argument."""
    ssh = [*PI2_ROOT_TO_PI3, f"otauser@{pi3_address}"]
    return ["taskset", "-c", "1-3", PY, "-m", "capstone_ota.probation", "--pi3-ssh", json.dumps(ssh),
            "--verdict-path", MONITOR_DIR + "/verdict.json", "--reset-path", MONITOR_DIR + "/monitor.reset",
            "--trace-file", f"{RUNTIME}/{trace}", "--probation", str(probation), "--safe", "5",
            "--max-wait", "300", "--max-total", str(max_total), "--poll", "2"]


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


def pi3_address(pi3):
    return pi3.run("ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1").stdout.strip()


def start_real_services(boards, log):
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
            f"nohup setsid {PY} -m capstone_ota.monitor.service --baseline {MONITOR_BASELINE} --out-dir {MONITOR_DIR} "
            f"> {MONITOR_DIR}/monitor.log 2>&1 < /dev/null & echo started")
    pi1.run("pkill -f '[p]lay_profile'; true", check=False)
    # No `cd ... &&` before the `&`: that backgrounds a subshell that keeps the ssh channel open forever.
    pi1.run(f"PYTHONPATH={RUNTIME} nohup setsid {PY} -m bench.play_profile --channel can0 "
            f"> {RUNTIME}/profile.log 2>&1 < /dev/null & echo started")
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


def feeder_command(pi3_address, interval=0.5):
    """Collects what the cockpit shows (cluster capture, Pi3 verdict, update progress). Display only: it never decides."""
    ssh = [*PI2_ROOT_TO_PI3, f"otauser@{pi3_address}"]
    return ["taskset", "-c", "1-3", "/usr/bin/python3", "-m", "bench.cockpit_feeder",
            "--cluster-socket", "/run/capstone-cluster/model.sock", "--pi3-ssh", json.dumps(ssh),
            "--verdict-path", MONITOR_DIR + "/verdict.json", "--interval", str(interval)]


def cockpit_ui_command():
    """The whole monitoring screen, fullscreen on the HDMI monitor's own desktop session (not the private Xvfb)."""
    return (f"cd {SCREEN}/cockpit 2>/dev/null; env QT_QPA_PLATFORM=wayland XDG_RUNTIME_DIR=/run/user/1000 "
            f"WAYLAND_DISPLAY=wayland-0 nohup setsid taskset -c 1-3 qmlscene {SCREEN}/cockpit/Cockpit.qml "
            f"> {SCREEN}/ui.log 2>&1 < /dev/null & echo started")


def start_screen(boards, pi3_ip, log, interval=0.1):
    pi2 = boards["pi2"]
    for name in ("__init__.py", "cockpit_feeder.py"):
        pi2.put(f"{SCREEN}/bench/{name}", (ROOT / "bench" / name).read_bytes())
    for name in COCKPIT_FILES:
        pi2.put(f"{SCREEN}/cockpit/{name}", (ROOT / "dashboard/cockpit" / name).read_bytes())
    site = f"{RUNTIME}/venv/lib/python3.11/site-packages"
    pi2.run("sudo systemctl stop field-cockpit-feeder.service 2>/dev/null; sudo rm -f /run/cockpit/extra.jsonl; true", check=False)
    pi2.run("sudo systemd-run --quiet --collect --unit field-cockpit-feeder "
            f"-p WorkingDirectory={SCREEN} -p Environment=PYTHONPATH={SCREEN}:{site} "
            "-p Environment=XAUTHORITY=/var/lib/capstone-app/Xauthority " + shlex.join(feeder_command(pi3_ip, interval)))
    pi2.run("pkill -f '[q]mlscene.*Cockpit.qml'; true", check=False)
    pi2.run(cockpit_ui_command())
    log("[screen] cockpit feeder + fullscreen monitoring screen started on Pi2's HDMI monitor")


def port_line(entry):
    """One short cockpit line for a gateway log entry, or None for noise (a bare port probe has no payload)."""
    if entry.get("event") == "gateway_closed":
        return "진단 포트 닫힘 · 이후 요청 거부"
    if entry.get("event") != "request" or not entry.get("slot"):
        return None
    source = str(entry.get("source", "?")).rsplit(":", 1)[0]
    if "gateway closed" in str(entry.get("response")):
        return f"진단 포트 · 닫힌 포트 접속 거부 ({source})"
    if entry.get("response") == "OK":
        return f"진단 포트 · 슬롯 {entry['slot']} 이미지 수신 ({source})"
    return f"진단 포트 · 요청 거부 ({source})"


def gateway_final_line():
    return f"진단 포트 {GATEWAY_PORT} 닫힘 · 게이트웨이 종료"


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


def stop_screen(boards):
    pi2 = boards["pi2"]
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


def pi2_status(pi2):
    # `status` exits 2 for a rolled_back device (a valid, stable state), so read the answer, not the exit code.
    done = pi2.run(f"cd {RUNTIME} && ./root-python -m capstone_ota --root {IVI_ROOT} status", check=False)
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise RuntimeError(f"cannot read Pi2 status: {done.stderr[-300:]}") from exc


def run(config, boards, output, verdict="forced", decision="human", attack=True, log=print, screen=False):
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
    if baseline["version"] != "2.0.0" or baseline["phase"] != "stable":
        raise SystemExit("Pi2 IVI is not at a stable V2 baseline: " + json.dumps(baseline))
    note("baseline", status=baseline)

    # Attack image: an older, validly signed IVI tree (the attacker already owns it).
    key = releases / "keys/ivi-private.pem"
    staging = output / "attack-payload"
    payload(staging, "V2")
    (staging / "old-release.txt").write_text("IVI 1.0.0 (downgrade image planted through the maintenance port)\n")
    pack(staging, output / "attack/v1", "1.0.0", 1, key)
    archive = output / "attack/v1/app.tar.gz"

    # Control centre on the laptop; Pi1 reports to it over Wi-Fi.
    order_key, order_pub = output / "order.key", output / "order.pub"
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    order_key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    order_key.chmod(0o600)
    order_pub.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo))
    server = IncidentServer((laptop, SERVER_PORT), releases / "releases", output / "incidents.json", order_key,
                            {"2.0.0": "v2"}, order_lifetime=600.0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server_url = f"http://{laptop}:{SERVER_PORT}"
    note("control_centre", url=server_url)

    gateway_stop = threading.Event()
    result = {"verdict_mode": verdict, "forced_verdict": verdict == "forced", "steps": steps}
    try:
        # Pi2: maintenance service on the Pi1 link only; gate pinned for exactly this run.
        pi2.run("sudo systemctl stop field-maintenance.service 2>/dev/null; true", check=False)
        pi2.run(f"sudo systemd-run --quiet --collect --unit field-maintenance {PY} -m capstone_ota.debug_port "
                f"--device-root {IVI_ROOT} --bind {PI2_ETH} --port {MAINTENANCE_PORT}")
        if verdict == "forced":
            trial, recheck = forced_failure(25), passing_recheck()
        else:
            start_real_services(boards, log)
            pi3_ip = pi3_address(boards["pi3"])
            if screen:
                start_screen(boards, pi3_ip, log)
                show_port(pi2, f"정비 포트 {MAINTENANCE_PORT} 열림 · 이더넷 전용")
            trial = probation_command(pi3_ip)
            recheck = probation_command(pi3_ip, probation=20, max_total=120, trace="recheck-trace.jsonl")
            seen = wait_for(lambda: healthy(pi3_verdict(boards["pi3"])), 90, 3.0)
            note("monitor_health", healthy=bool(seen), verdict=pi3_verdict(boards["pi3"]))
            if not seen:
                raise SystemExit("the Pi3 monitor does not see both the vehicle input and the cluster answer")
        pi2.write(RUNTIME + "/gate.json", json.dumps(gate_config(trial, recheck)))

        # Pi1: target, keys, and the one process that runs gateway + relay + responder.
        pi1.write(RUNTIME + "/ivi-public.pem", (releases / "keys/ivi-public.pem").read_text())
        pi1.write(RUNTIME + "/order-public.pem", order_pub.read_text())
        pi1.write(RUNTIME + "/target.json", json.dumps(target_for(trial, recheck)))
        pi1.run(f"rm -f {RUNTIME}/result.json {RUNTIME}/gateway.jsonl {RUNTIME}/central.log")
        command = shlex.join([
            PY, "-m", "capstone_ota.central_incident", "--server", server_url, "--vehicle", "PI2-001",
            "--target", RUNTIME + "/target.json", "--release-url", server_url + "/releases/v3",
            "--release-public-key", RUNTIME + "/ivi-public.pem", "--order-public-key", RUNTIME + "/order-public.pem",
            "--listen", f"0.0.0.0:{GATEWAY_PORT}", "--upstream", f"{PI2_ETH}:{MAINTENANCE_PORT}",
            "--gateway-log", RUNTIME + "/gateway.jsonl", "--result-out", RUNTIME + "/result.json",
            "--bind", PI1_ETH, "--advertise", PI1_ETH, "--decision-timeout", "900", "--linger", "60"])
        pi1.run(f"nohup setsid {command} > {RUNTIME}/central.log 2>&1 < /dev/null & echo started")
        pi1_wifi = pi1.run("ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1").stdout.strip()
        if not wait_for(lambda: reachable(pi1_wifi, GATEWAY_PORT), 30):
            raise SystemExit("Pi1 gateway did not come up: " + pi1.run(f"tail -5 {RUNTIME}/central.log", check=False).stdout)
        pi2_wifi = pi2.run("ip -4 -o addr show wlan0 | awk '{print $4}' | cut -d/ -f1").stdout.strip()
        note("exposure", gateway_reachable_from_laptop=reachable(pi1_wifi, GATEWAY_PORT),
             pi2_maintenance_reachable_from_laptop=reachable(pi2_wifi, MAINTENANCE_PORT),
             pi2_maintenance_listening_on=pi2.run(f"ss -ltn | grep ':{MAINTENANCE_PORT} ' || true").stdout.split()[3:4])

        if screen:
            show_port(pi2, f"진단 포트 {GATEWAY_PORT} 열림 · Wi-Fi에서 접속 가능")
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
            result["attack_response"] = push(pi1_wifi, GATEWAY_PORT, "A", archive)
            note("attack_through_gateway", response=result["attack_response"])
        if verdict == "real":
            time.sleep(8)
            pi2.run(f"touch {MAP_FLAG}")  # the map screen opens: V3's background workers start
            note("map_opened", flag=MAP_FLAG)
        incident = wait_for(lambda: server.incidents, 240 if verdict == "forced" else 600) if attack else None
        if attack and not incident:
            raise SystemExit("no incident reached the control centre")
        if attack:
            note("incident_reported", id=incident[0]["id"], status=incident[0]["status"])
        if not attack:
            pass
        elif decision == "human":
            note("waiting_for_human", url=server_url + "/")
        else:
            import http.client
            connection = http.client.HTTPConnection(laptop, SERVER_PORT, timeout=10)
            connection.request("POST", f"/incidents/{incident[0]['id']}/{'approve' if decision == 'approve' else 'reject'}")
            connection.getresponse().read()
            connection.close()
            note("auto_decision", decision=decision)
        done = wait_for(lambda: pi1.run(f"cat {RUNTIME}/result.json 2>/dev/null", check=False).stdout.strip(), 1200, 2.0)
        if not done:
            raise SystemExit("Pi1 produced no result: " + pi1.run(f"tail -8 {RUNTIME}/central.log", check=False).stdout)
        result["pi1"] = json.loads(done)
        watching["on"] = False
        result["verdict_timeline"] = timeline
        if attack:
            result["reattack_response"] = push(pi1_wifi, GATEWAY_PORT, "A", archive)
            note("reattack_through_closed_gate", response=result["reattack_response"])
        result["gateway_log"] = [json.loads(line) for line in pi1.run(f"cat {RUNTIME}/gateway.jsonl").stdout.splitlines()]
        result["pi2_final"] = pi2_status(pi2)
        result["incident_record"] = server.incidents
        note("final", status=result["pi2_final"])
    finally:
        server.shutdown()
        server.server_close()
        pi2.run("sudo systemctl stop field-maintenance.service 2>/dev/null; true", check=False)
        if screen:
            time.sleep(2.5)  # let the watcher pass on the last gateway entries (the refused re-attack) first
        pi1.run("pkill -f '[c]apstone_ota.central_incident'; true", check=False)
        gateway_stop.set()
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "run", "teardown"])
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verdict", choices=["forced", "real"], default="forced")
    parser.add_argument("--decision", choices=["human", "approve", "reject"], default="human")
    parser.add_argument("--no-attack", action="store_true", help="only watch the real detection and the normal rollback")
    parser.add_argument("--screen", action="store_true", help="show the whole cockpit on Pi2's HDMI monitor (needs --verdict real)")
    args = parser.parse_args(argv)
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
        outcome = run(config, boards, args.output, args.verdict, args.decision, attack=not args.no_attack, screen=args.screen)
        print(json.dumps({"final": outcome.get("pi2_final"), "forced_verdict": outcome["forced_verdict"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
