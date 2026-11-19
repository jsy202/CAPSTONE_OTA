"""The forced command on Pi2's authorized_keys line: Pi1's key may run one pinned repair, nothing else."""
import json
import shlex

import pytest

from capstone_ota import repair_gate

CONFIG = {"checkout": "/home/otauser/capstone-incident", "python": "/home/otauser/capstone-incident/root-python",
          "root": "/opt/capstone-ivi/device", "check_command": ["/usr/bin/python3", "-m", "x.check", "--flag"],
          "central_host": "10.77.0.1"}


def command(url="http://10.77.0.1:41234", **changes):
    parts = {"python": CONFIG["python"], "root": CONFIG["root"], "check": json.dumps(CONFIG["check_command"])}
    parts.update(changes)
    return ("cd " + shlex.quote(CONFIG["checkout"]) + " && " + shlex.join(
        [parts["python"], "-m", "capstone_ota", "--root", parts["root"], "ivi-repair",
         "--check-command", parts["check"], url]))


def test_the_pinned_repair_is_allowed_and_becomes_an_argv():
    argv = repair_gate.allowed(command(), CONFIG)
    assert argv == [CONFIG["python"], "-m", "capstone_ota", "--root", CONFIG["root"], "ivi-repair",
                    "--check-command", json.dumps(CONFIG["check_command"]), "http://10.77.0.1:41234"]


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
    "$(id) " + command(),
])
def test_everything_else_is_refused(original):
    assert repair_gate.allowed(original, CONFIG) is None


def test_main_refuses_without_an_original_command(tmp_path, monkeypatch, capsys):
    (tmp_path / "gate.json").write_text(json.dumps(CONFIG))
    monkeypatch.delenv("SSH_ORIGINAL_COMMAND", raising=False)
    assert repair_gate.main(["--config", str(tmp_path / "gate.json")]) == 126
    assert "refused" in capsys.readouterr().err


def test_main_refuses_a_bad_config(tmp_path, monkeypatch):
    (tmp_path / "gate.json").write_text(json.dumps({"checkout": "relative"}))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", command())
    assert repair_gate.main(["--config", str(tmp_path / "gate.json")]) == 126


def test_the_responders_real_command_passes_the_gate():
    """Agreement test: what Pi1 sends is exactly what Pi2's gate pins (otherwise only a real board would notice)."""
    from capstone_ota.incident_responder import IncidentResponder
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = {"host": "pi2", "checkout": CONFIG["checkout"], "python": CONFIG["python"],
                        "root": CONFIG["root"], "public_key": "/opt/key.pub", "check_command": CONFIG["check_command"],
                        "application": "ivi"}
    ssh = responder._repair_command("http://10.77.0.1:5555")
    assert repair_gate.allowed(ssh[-1], CONFIG) is not None
