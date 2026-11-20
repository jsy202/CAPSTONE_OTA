"""The forced command on Pi2's authorized_keys line: Pi1's key may run the pinned update or repair, nothing else."""
import json
import shlex

import pytest

from capstone_ota import ota_gate

CHECK = ["/usr/bin/python3", "-m", "x.check", "--flag"]
CONFIG = {"checkout": "/home/otauser/capstone-incident", "python": "/home/otauser/capstone-incident/root-python",
          "root": "/opt/capstone-ivi/device", "central_host": "10.77.0.1",
          "repair": {"check_command": CHECK},
          "update": {"public_key": "/opt/capstone-ivi/ivi-public.pem", "check_command": CHECK}}


def command(url="http://10.77.0.1:41234", **changes):
    parts = {"python": CONFIG["python"], "root": CONFIG["root"], "check": json.dumps(CHECK)}
    parts.update(changes)
    return ("cd " + shlex.quote(CONFIG["checkout"]) + " && " + shlex.join(
        [parts["python"], "-m", "capstone_ota", "--root", parts["root"], "ivi-repair",
         "--check-command", parts["check"], url]))


def update_command(url="http://10.77.0.1:41234", **changes):
    parts = {"public_key": CONFIG["update"]["public_key"], "check": json.dumps(CHECK)}
    parts.update(changes)
    return ("cd " + shlex.quote(CONFIG["checkout"]) + " && " + shlex.join(
        [CONFIG["python"], "-m", "capstone_ota", "--root", CONFIG["root"], "ivi-update", "--public-key",
         parts["public_key"], "--check-command", parts["check"], url]))


def test_the_pinned_update_is_allowed():
    argv = ota_gate.allowed(update_command(), CONFIG)
    assert argv[5] == "ivi-update" and argv[-1] == "http://10.77.0.1:41234"


def test_an_action_without_a_pinned_config_is_refused():
    only_repair = {key: value for key, value in CONFIG.items() if key != "update"}
    assert ota_gate.allowed(update_command(), only_repair) is None
    assert ota_gate.allowed(command(), only_repair) is not None


def test_the_pinned_repair_is_allowed_and_becomes_an_argv():
    argv = ota_gate.allowed(command(), CONFIG)
    assert argv == [CONFIG["python"], "-m", "capstone_ota", "--root", CONFIG["root"], "ivi-repair",
                    "--check-command", json.dumps(CHECK), "http://10.77.0.1:41234"]


@pytest.mark.parametrize("original", [
    None,
    "",
    "id",
    "cd /home/otauser/capstone-incident && /bin/sh",
    command() + " ; id",
    command() + " && id",
    command(url="http://10.77.0.1:41234; id"),
    command(url="http://192.0.2.9:41234"),            # not Central's address
    command(url="http://10.77.0.1:41234/x"),          # not the bare relay URL
    command(url="https://10.77.0.1:41234"),
    command(url="http://10.77.0.1:99999999"),
    command(root="/opt/capstone-ivi/other"),           # a different device
    command(python="/bin/sh"),
    command(check=json.dumps(["/bin/true"])),          # a different (weaker) re-check
    command().replace("ivi-repair", "ivi-update"),     # update is not repair
    "cd /tmp && " + command().split(" && ", 1)[1],     # another working directory
    command().replace(" --check-command", " --initialization-only --check-command"),
    update_command(public_key="/tmp/attacker.pub"),    # an update must be verified with the pinned key
    update_command(check=json.dumps(["/bin/true"])),
    update_command().replace(" --public-key", " --systemd --public-key"),
    update_command(url="http://192.0.2.9:41234"),
    "$(id) " + command(),
])
def test_everything_else_is_refused(original):
    assert ota_gate.allowed(original, CONFIG) is None


def test_main_refuses_without_an_original_command(tmp_path, monkeypatch, capsys):
    (tmp_path / "gate.json").write_text(json.dumps(CONFIG))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    assert ota_gate.main(["--config", str(tmp_path / "gate.json")]) == 126
    assert "refused" in capsys.readouterr().err


def test_main_refuses_a_bad_config(tmp_path, monkeypatch):
    (tmp_path / "gate.json").write_text(json.dumps({"checkout": "relative"}))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command())
    assert ota_gate.main(["--config", str(tmp_path / "gate.json")]) == 126


def test_the_relays_real_update_command_passes_the_gate():
    from capstone_ota.relay import target_command
    target = {"host": "pi2", "checkout": CONFIG["checkout"], "python": CONFIG["python"], "root": CONFIG["root"],
              "public_key": CONFIG["update"]["public_key"], "check_command": CHECK, "application": "ivi"}
    original = "cd " + shlex.quote(CONFIG["checkout"]) + " && " + shlex.join([*target_command(target), "http://10.77.0.1:7"])
    assert ota_gate.allowed(original, CONFIG) is not None


def test_the_responders_real_command_passes_the_gate():
    """Agreement test: what Pi1 sends is exactly what Pi2's gate pins (otherwise only a real board would notice)."""
    from capstone_ota.incident_responder import IncidentResponder
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = {"host": "pi2", "checkout": CONFIG["checkout"], "python": CONFIG["python"],
                        "root": CONFIG["root"], "public_key": "/opt/key.pub", "check_command": CHECK,
                        "application": "ivi"}
    ssh = responder._repair_command("http://10.77.0.1:5555")
    assert ota_gate.allowed(ssh[-1], CONFIG) is not None
