"""Forced command for Pi1's key on Pi2: it may run exactly the pinned `ivi-update` or `ivi-repair`, nothing else.

authorized_keys line on Pi2 (see docs/GATEWAY_INCIDENT_RESPONSE.md):

    from="<Pi1 address>",restrict,command="<python> -m capstone_ota.ota_gate --config <gate.json>" ssh-ed25519 ...

Pi1's relay and incident responder send `cd CHECKOUT && PYTHON -m capstone_ota --root ROOT <action> ... URL`.
The gate rebuilds that exact string from its own pinned config and runs it only on an exact match, so a
stolen key cannot start a shell, another device, a weaker re-check, another verification key or a download
from anywhere but Central. Repair itself still accepts only the committed image; update still obeys the
version and svn rules. An action missing from the config is refused.

gate.json: {"checkout", "python", "root", "central_host",
            "update": {"public_key", "check_command", "recheck_command" (optional)}, "repair": {"check_command"}}
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys


def _absolute(value) -> bool:
    return isinstance(value, str) and value.startswith("/") and "\x00" not in value


def _check(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(part, str) and part for part in value)


def _valid(config) -> bool:
    return (isinstance(config, dict)
            and all(_absolute(config.get(key)) for key in ("checkout", "python", "root"))
            and isinstance(config.get("central_host"), str)
            and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", config["central_host"])))


def _argv(config: dict, action: str, url: str) -> list[str] | None:
    pinned = config.get(action)
    if not isinstance(pinned, dict) or not _check(pinned.get("check_command")):
        return None
    head = [config["python"], "-m", "capstone_ota", "--root", config["root"]]
    check = ["--check-command", json.dumps(pinned["check_command"])]
    if action == "repair":
        return [*head, "ivi-repair", *check, url]
    if action == "update" and _absolute(pinned.get("public_key")):
        recheck, extra = pinned.get("recheck_command"), []
        if recheck is not None:
            if not _check(recheck):
                return None
            extra = ["--recheck-command", json.dumps(recheck)]
        return [*head, "ivi-update", "--public-key", pinned["public_key"], *check, *extra, url]
    return None


def allowed(original, config) -> list[str] | None:
    """The argv to run if `original` is exactly a pinned request, else None."""
    if not isinstance(original, str) or not original or not _valid(config):
        return None
    url = original.rsplit(" ", 1)[-1]
    port = re.fullmatch(r"http://" + re.escape(config["central_host"]) + r":([0-9]{1,5})", url)
    if not port or int(port.group(1)) > 65535:
        return None
    for action in ("update", "repair"):
        argv = _argv(config, action, url)
        if argv is not None and original == "cd " + shlex.quote(config["checkout"]) + " && " + shlex.join(argv):
            return argv
    return None


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
        print("refused: this key may only run the pinned update or repair", file=sys.stderr)
        return 126
    os.chdir(config["checkout"])
    os.execv(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
