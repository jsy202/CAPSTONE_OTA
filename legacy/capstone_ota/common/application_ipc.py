"""Newline-delimited JSON v1 over a local Unix socket to a managed application.

One connection carries one request and one response. Requests are
{schema_version:1, request_id:<random hex>, operation, payload}; responses echo
request_id and carry ok plus an operation result. A mismatched request_id is a
stale or foreign answer and fails closed. Functional results are the values
the application actually interpreted/displayed, never an echo of the request.
"""
from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path

from capstone_ota.agent.zonal import ApplicationObservation
from capstone_ota.common.can_protocol import FunctionalTestRequestFrame, Gear, VehicleStatusFrame
from capstone_ota.common.errors import OtaError


_MAX_RESPONSE = 4096
_SIGNALS = {"speed", "rpm", "gear", "warnings"}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class ApplicationIpc:
    def __init__(self, path: Path, timeout_s: float = 2.0):
        if timeout_s <= 0:
            raise ValueError("IPC timeout must be positive")
        self.path, self.timeout_s = Path(path), timeout_s

    def _call(self, operation: str, payload: dict, timeout_s: float | None = None) -> dict:
        request_id = uuid.uuid4().hex
        request = {"schema_version": 1, "request_id": request_id, "operation": operation, "payload": payload}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                stream.settimeout(min(self.timeout_s, timeout_s) if timeout_s else self.timeout_s)
                stream.connect(str(self.path))
                stream.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
                raw = stream.makefile("rb").readline(_MAX_RESPONSE + 1)
        except OSError as exc:
            raise OtaError("APPLICATION_IPC_FAILED", "cannot communicate with application") from exc
        try:
            if len(raw) > _MAX_RESPONSE or not raw.endswith(b"\n"):
                raise ValueError("response is truncated or too large")
            response = json.loads(raw, object_pairs_hook=_unique)
            if (not isinstance(response, dict) or set(response) != {"schema_version", "request_id", "ok", "result"}
                    or type(response["schema_version"]) is not int or response["schema_version"] != 1):
                raise ValueError("invalid IPC response envelope")
        except (ValueError, UnicodeError) as exc:
            raise OtaError("APPLICATION_IPC_INVALID", str(exc)) from exc
        if response["request_id"] != request_id:
            raise OtaError("APPLICATION_IPC_INVALID", "response does not answer this request")
        if response["ok"] is not True:
            raise OtaError("APPLICATION_IPC_REJECTED", f"application rejected {operation}")
        return response["result"]

    def observe_functional_test(self, request: FunctionalTestRequestFrame) -> ApplicationObservation:
        result = self._call("functional", {"speed": request.speed, "rpm": request.rpm, "gear": int(request.gear),
                                           "warnings": request.warnings, "test_id": request.test_id})
        try:
            if not isinstance(result, dict) or set(result) != _SIGNALS or any(type(v) is not int for v in result.values()):
                raise ValueError("functional result must contain exactly four integer signals")
            return ApplicationObservation(result["speed"], result["rpm"], Gear(result["gear"]), result["warnings"])
        except ValueError as exc:
            raise OtaError("APPLICATION_IPC_INVALID", str(exc)) from exc

    def apply_vehicle_status(self, sample: VehicleStatusFrame) -> None:
        self._call("vehicle", {"speed": sample.speed, "rpm": sample.rpm, "gear": int(sample.gear),
                               "warnings": sample.warnings})

    def set_maintenance(self, enabled: bool, *, timeout_s: float) -> None:
        result = self._call("maintenance", {"enabled": enabled}, timeout_s)
        if result != {"enabled": enabled}:
            raise OtaError("APPLICATION_IPC_INVALID", "application did not confirm maintenance mode")
