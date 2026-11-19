"""Gateway incident drill: a maintenance-port attack through Pi1, a refused V1, a human decision.

Everything runs as local threads/processes on this machine (see `scope` in the result). The V3
trial failure is a FORCED verdict standing in for Pi3, not a real CPU-contention measurement.

  python -m demo.incident_drill run --output .demo/incident-drill            # you click in the browser
  python -m demo.incident_drill run --output .demo/incident-drill --auto-approve
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
import http.client

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from capstone_ota.bundle import pack
from capstone_ota.debug_port import DebugPortServer, push
from capstone_ota.gateway import DiagnosticGateway
from capstone_ota.incident_responder import IncidentResponder
from capstone_ota.incident_server import IncidentServer
from capstone_ota.ivi import IviDevice
from dashboard.ivi.build import payload

ROOT = Path(__file__).resolve().parents[1]
PASS_CHECK = [sys.executable, "-m", "demo.restore_check"]  # looks at the restored slot; not a constant yes

# Forced Pi3 stand-in. Call 0 blesses the V2 baseline. Call 1 is V3's trial: while it is
# "evaluating", the attacker pushes the old V1 image into the recovery slot THROUGH PI1'S
# GATEWAY, then the verdict is a forced failure. Later calls (the restored V2) pass.
FORCED_VERDICT = (
    "import sys, json, pathlib\n"
    "counter = pathlib.Path(sys.argv[1])\n"
    "n = int(counter.read_text()) if counter.exists() else 0\n"
    "counter.write_text(str(n + 1))\n"
    "if n == 1:\n"
    "    from capstone_ota.debug_port import push\n"
    "    answer = push('127.0.0.1', int(sys.argv[2]), 'A', sys.argv[3])\n"
    "    pathlib.Path(sys.argv[1] + '.attack').write_text(answer)\n"
    "print(json.dumps({'passed': n != 1}))\n"
)


def _write_keypair(directory: Path, name: str):
    private = Ed25519PrivateKey.generate()
    key, public = directory / f"{name}.key", directory / f"{name}.pub"
    key.touch(mode=0o600)
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, public


def _operator(server, action):
    """Stands in for the human clicking the button, when the drill runs unattended."""
    def click():
        deadline = time.time() + 120
        while time.time() < deadline and not server.incidents:
            time.sleep(0.1)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("POST", f"/incidents/{server.incidents[0]['id']}/{action}")
        connection.getresponse().read()
        connection.close()
    threading.Thread(target=click, daemon=True).start()


def run(output, decision="human"):
    if decision not in {"human", "approve", "reject"}:
        raise ValueError("decision must be human, approve or reject")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    steps = []

    def note(step, **detail):
        steps.append({"time": time.time(), "step": step, **detail})

    keys = output / "keys"
    keys.mkdir(mode=0o700)
    release_key, release_pub = _write_keypair(keys, "release")
    order_key, order_pub = _write_keypair(keys, "control")
    marker = output / "old-app-executed"
    releases = output / "releases"
    for label, version, svn in (("v1", "1.0.0", 1), ("v2", "2.0.0", 2), ("v3", "3.0.0", 2)):
        app = payload(output / "payloads" / label, "V3" if label == "v3" else "V2")
        if label == "v1":  # An authentic, signed, older image; if it ever runs it leaves a marker.
            config = json.loads((app / "ivi.json").read_text())
            (app / "ivi.json").write_text(json.dumps(dict(config, version="V1")))
            wrapper = app / "bin/app"
            wrapper.write_text(wrapper.read_text().replace("exec python3", "touch " + shlex.quote(str(marker)) + "\nexec python3"))
        pack(app, releases / label, version, svn, release_key)
    note("releases_signed", labels=["v1", "v2", "v3"])

    shim_dir = output / "local-transport"
    shim_dir.mkdir()
    (shim_dir / "ssh").write_text('#!/usr/bin/env python3\nimport os,sys\nos.execv("/bin/sh",["sh","-c",sys.argv[-1]])\n')
    (shim_dir / "ssh").chmod(0o755)
    previous_path = os.environ["PATH"]
    os.environ["PATH"] = f"{shim_dir}:{previous_path}"

    pi2_root = output / "pi2-ivi"
    pi2_root.mkdir()
    server = IncidentServer(("127.0.0.1", 0), releases, output / "incidents.json", order_key, {"2.0.0": "v2"})
    maintenance = DebugPortServer(("127.0.0.1", 0), pi2_root)  # Pi2's service: loopback only.
    gateway = DiagnosticGateway(("127.0.0.1", 0), maintenance.server_address, output / "gateway.jsonl")
    servers = [server, maintenance, gateway]
    for running in servers:
        threading.Thread(target=running.serve_forever, daemon=True).start()
    server_url = f"http://127.0.0.1:{server.server_address[1]}"
    gateway_port = gateway.server_address[1]
    try:
        note("services_up", control_centre=server_url, gateway_port=gateway_port)
        if decision == "human":
            print(f"승인 페이지: {server_url}/  (사고가 접수되면 여기서 버튼을 누르세요)", file=sys.stderr)
        else:
            _operator(server, "approve" if decision == "approve" else "reject")

        counter = output / "verifier-calls"
        device = IviDevice(pi2_root, verifier_command=[sys.executable, "-c", FORCED_VERDICT, str(counter),
                                                      str(gateway_port), str(releases / "v1/app.tar.gz")])
        device.update(f"{server_url}/releases/v2", public_key=release_pub)
        note("v2_committed", status=device.status())
        try:
            device.update(f"{server_url}/releases/v3")
        except ValueError as exc:
            note("v3_trial_ended", error=str(exc))
        status = dict(device.status(), **device.recovery_evidence())  # what the CLI answer carries
        if status["result"] != "recovery_failed":
            raise ValueError("the attack did not leave the device in recovery_failed: " + json.dumps(status))
        attack_response = (counter.with_name(counter.name + ".attack")).read_text()
        note("pi2_refused_tampered_recovery", status=status, attack_response=attack_response)

        target = {"host": "local-pi2", "checkout": str(ROOT), "python": sys.executable, "root": str(pi2_root),
                  "public_key": str(release_pub), "check_command": PASS_CHECK, "application": "ivi"}
        responder = IncidentResponder(server_url, "PI2-001", order_pub, release_pub, gateway, target,
                                      decision_timeout=900.0, poll_interval=0.5)
        note("pi1_handling_incident")
        response = responder.handle(status)
        note("incident_resolved", action=response["action"], reason=response["reason"] if "reason" in response else "")

        reattack = push("127.0.0.1", gateway_port, "A", releases / "v1/app.tar.gz")
        note("reattack_attempted", response=reattack)
        result = {"scope": "local processes; Pi1 routing is configuration, not network isolation",
                  "forced_verdict": True, "decision": decision, "steps": steps,
                  "attack_response": attack_response, "reattack_response": reattack,
                  "gateway_log": gateway.log_tail(50), "response": response, "final": device.status(),
                  "v1_executed": marker.exists(), "incident": server.incidents[0]}
        (output / "result.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        os.environ["PATH"] = previous_path
        for running in servers:
            running.shutdown()
            running.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    drill = commands.add_parser("run")
    drill.add_argument("--output", required=True, type=Path)
    choice = drill.add_mutually_exclusive_group()
    choice.add_argument("--auto-approve", action="store_true")
    choice.add_argument("--auto-reject", action="store_true")
    args = parser.parse_args(argv)
    decision = "approve" if args.auto_approve else "reject" if args.auto_reject else "human"
    try:
        result = run(args.output, decision)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"action": result["response"]["action"], "final": result["final"]["result"],
                      "v1_executed": result["v1_executed"]}))
    print("Evidence:", args.output / "result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
