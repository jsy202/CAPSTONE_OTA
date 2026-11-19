"""Pi1's incident response: close the diagnostic gate, report to the control centre, and restore
the committed release on Pi2 only after a valid, single-use, human-approved signed order.

Nothing here decides *what* may run. Pi2's repair() accepts only the exact committed image; this
module only decides *whether to ask*, and refuses to ask without a verified approval.
"""
from __future__ import annotations

import base64
from http.client import HTTPException
import json
from pathlib import Path
import re
import secrets
import shlex
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .bundle import verify
from .ivi import IviDevice
from .order import verify_order
from .process import exchange
from .relay import cache_release, serving_release, target_command


class IncidentResponder:
    def __init__(self, server_url: str, vehicle: str, order_public_key: Path, release_public_key: Path,
                 gateway, target: dict, decision_timeout: float = 300.0, poll_interval: float = 1.0,
                 bind: str = "127.0.0.1", advertise: str | None = None,
                 api_token: str | None = None):
        self.server_url = server_url.rstrip("/")
        self.vehicle = vehicle
        self.order_public_key = Path(order_public_key)
        self.release_public_key = Path(release_public_key)
        self.gateway = gateway
        self.target = target
        self.decision_timeout = decision_timeout
        self.poll_interval = poll_interval
        self.bind, self.advertise = bind, advertise
        self.api_token = api_token
        self._used: set[tuple[str, str]] = set()

    # --- public -----------------------------------------------------------------------------
    def handle(self, result: dict) -> dict:
        """React to a Pi2 result. Only `recovery_failed` starts an incident."""
        if result.get("result") != "recovery_failed":
            return {"action": "none"}
        self.gateway.close("device reported recovery_failed")
        nonce = secrets.token_hex(16)
        report = self._report(result, nonce)
        if report is None:
            return self._stop(None, "the device result names no committed version")
        try:
            _status, data = self._call("POST", "/incidents", report)
            answer = json.loads(data)
            incident = answer.get("id") if isinstance(answer, dict) else None
            if not isinstance(incident, str) or not re.fullmatch(r"INC-[0-9]{4,}", incident):
                raise ValueError("the control centre did not return an incident id")
        except (OSError, ValueError, HTTPException) as exc:
            return self._stop(None, f"control centre unreachable or invalid: {exc}")
        order = self._await_order(incident, nonce, report["committed_version"])
        if order is None:
            return self._stop(incident, "no valid decision arrived before the timeout")
        if order["action"] == "reject":
            self._outcome(incident, "maintenance_required")
            return self._stop(incident, "the control centre rejected the restore")
        return self._restore(incident, order)

    def accept_order(self, incident: str, nonce: str, body: dict, version: str | None = None) -> dict:
        """Verify one order body from the server. Each (incident, nonce) order works once."""
        try:
            raw, signature = base64.b64decode(body["order"], validate=True), base64.b64decode(body["signature"], validate=True)
        except (KeyError, TypeError) as exc:
            raise ValueError("malformed order response") from exc
        order = verify_order(raw, signature, self.order_public_key, vehicle=self.vehicle, nonce=nonce, now=time.time())
        if order["incident"] != incident:
            raise ValueError("order is for another incident")
        if order["action"] == "restore" and version is not None and order["version"] != version:
            raise ValueError("order version differs from the version this vehicle reported")
        key = (order["incident"], order["nonce"])
        if key in self._used:
            raise ValueError("order was already used")
        self._used.add(key)
        return order

    # --- helpers ----------------------------------------------------------------------------
    def _stop(self, incident, reason, repair=None):
        return {"action": "maintenance_required", "incident": incident, "repair": repair, "reason": reason}

    def _call(self, method: str, path: str, body=None) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        request = Request(self.server_url + path, data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def _outcome(self, incident: str, result: str) -> None:
        try:
            self._call("POST", f"/incidents/{incident}/outcome", {"result": result})
        except (OSError, HTTPException):
            pass  # The report is best effort; the device state is the record.

    def _report(self, result: dict, nonce: str):
        # Everything about Pi2 comes from Pi2's own answer: on a real vehicle Pi1 cannot read its files.
        version = result.get("version")
        if not version:
            return None
        source = next((entry.get("source") for entry in reversed(self.gateway.log_tail(100))
                       if entry.get("event") == "request" and entry.get("forwarded")), None)
        return {"vehicle": self.vehicle, "nonce": nonce, "time": time.time(), "type": "recovery_slot_tamper",
                "slot": result.get("active_slot"), "committed_version": version,
                "committed_svn": result.get("min_svn"),
                "expected_tree_sha256": result.get("expected_tree_sha256"),
                "observed_tree_sha256": result.get("observed_tree_sha256"),
                "detail": result.get("error"), "source": source,
                "gateway_state": "open" if self.gateway.is_open else "closed",
                "gateway_log_tail": self.gateway.log_tail(10), "cluster": "unaffected"}

    def _await_order(self, incident: str, nonce: str, version: str):
        deadline = time.monotonic() + self.decision_timeout
        while time.monotonic() < deadline:
            try:
                status, data = self._call("GET", f"/incidents/{incident}/order")
                if status == 200:
                    return self.accept_order(incident, nonce, json.loads(data), version)
            except (OSError, ValueError, HTTPException):
                pass  # An invalid or unreachable answer is ignored; a later genuine one may still arrive.
            time.sleep(self.poll_interval)
        return None

    def _repair_command(self, relay_url: str) -> list[str]:
        arguments = target_command(self.target)  # Validates the target exactly like `forward`.
        application = self.target.get("application", "cluster")
        arguments[arguments.index(application + "-update")] = application + "-repair"
        command, skip = [], False
        for argument in arguments:
            if skip:
                skip = False
            elif argument == "--public-key":
                skip = True  # repair uses the key pinned on the device
            elif argument != "--systemd":
                command.append(argument)
        if "repair_check_command" in self.target:
            # The update's check judges a trial; the re-check after a restore is a different command.
            command[command.index("--check-command") + 1] = json.dumps(self.target["repair_check_command"])
        remote = "cd " + shlex.quote(self.target["checkout"]) + " && " + shlex.join([*command, relay_url])
        return ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=5",
                self.target["host"], remote]

    def _restore(self, incident: str, order: dict) -> dict:
        try:
            with tempfile.TemporaryDirectory(prefix="capstone-restore-") as temporary:
                cache = cache_release(f"{self.server_url}/releases/{order['release']}",
                                      self.release_public_key, Path(temporary) / "release")
                metadata = verify((cache / "release.json").read_bytes(), (cache / "release.sig").read_bytes(),
                                  self.release_public_key)
                if metadata["version"] != order["version"]:
                    raise ValueError("the served release version does not match the ordered version")
                with serving_release(cache, self.bind, self.advertise) as relay_url:
                    deadline = 2 * IviDevice.verifier_timeout + 300 if self.target.get("application") == "ivi" else 300
                    rows, _pid = exchange(self._repair_command(relay_url), [], timeout=deadline, allow_rollback=True)
            repair = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None
            if repair is None or repair.get("result") != "rolled_back":
                raise ValueError("the device did not report a completed restore")
        except (OSError, ValueError, HTTPException) as exc:
            self._outcome(incident, "repair_failed")
            return self._stop(incident, f"restore failed: {exc}")
        self._outcome(incident, "rolled_back")
        return {"action": "restored", "incident": incident, "repair": repair, "reason": ""}
