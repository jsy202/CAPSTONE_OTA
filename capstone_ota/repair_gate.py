"""Forced command for Pi1's key on Pi2: it may run exactly one pinned `ivi-repair`, nothing else.

authorized_keys line on Pi2 (see docs/GATEWAY_INCIDENT_RESPONSE.md):

    from="<Pi1 address>",restrict,command="<python> -m capstone_ota.repair_gate --config <gate.json>" ssh-ed25519 ...

The incident responder asks for `cd CHECKOUT && PYTHON -m capstone_ota --root ROOT ivi-repair
--check-command JSON URL`. The gate rebuilds that exact string from its own pinned config and runs it
only on an exact match, so a stolen key cannot start a shell, another update, another device, a weaker
re-check or a download from anywhere but Central. Repair itself still accepts only the committed image.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys

REQUIRED = {"checkout", "python", "root", "check_command", "central_host"}


def _valid(config) -> bool:
    return (isinstance(config, dict) and REQUIRED <= config.keys()
            and all(isinstance(config[key], str) and config[key].startswith("/") for key in ("checkout", "python", "root"))
            and isinstance(config["check_command"], list) and config["check_command"]
            and all(isinstance(part, str) and part for part in config["check_command"])
            and isinstance(config["central_host"], str)
            and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", config["central_host"])))


def _argv(config: dict, url: str) -> list[str]:
    return [config["python"], "-m", "capstone_ota", "--root", config["root"], "ivi-repair",
            "--check-command", json.dumps(config["check_command"]), url]


def allowed(original, config) -> list[str] | None:
    """The argv to run if `original` is exactly the pinned repair request, else None."""
    if not isinstance(original, str) or not original or not _valid(config):
        return None
    url = original.rsplit(" ", 1)[-1]
    if not re.fullmatch(r"http://" + re.escape(config["central_host"]) + r":[0-9]{1,5}", url) or int(url.rsplit(":", 1)[1]) > 65535:
        return None
    argv = _argv(config, url)
    expected = "cd " + shlex.quote(config["checkout"]) + " && " + shlex.join(argv)
    return argv if original == expected else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text())
    except (OSError, ValueError):
        config = None
    command = allowed(os.environ.get("SSH_ORIGINAL_COMMAND"), config)
    if command is None:
        print("refused: this key may only run the pinned repair", file=sys.stderr)
        return 126
    os.chdir(config["checkout"])
    os.execv(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
