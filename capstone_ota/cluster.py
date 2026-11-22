"""Target-side application installation; no Central slots or existing Qt services."""
import subprocess
import math
import time
from pathlib import Path

from . import can_verdict
from .device import Device
from .process import exchange


def check_command(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(part, str) or not part or "\x00" in part for part in value)):
        raise ValueError("check command must be a nonempty JSON array of arguments")
    return list(value)


class ClusterDevice(Device):
    service_name = "capstone-cluster.service"   # subclasses manage another service with the same A/B logic
    companion_services = ()                      # other applications of the same OTA image: stopped/restarted with it on a switch
    verifier_timeout = 60                        # seconds an external verifier may take

    def __init__(self, root, verifier_command=None, recovery_verification=True,
                 verifier_can_channel=None, verifier_can_secret_path=None, recheck_command=None):
        super().__init__(root, recovery_verification=recovery_verification)
        self.verifier_command = check_command(verifier_command) if verifier_command is not None else None
        # The check of the old image after a rollback may be its own, shorter command; without one the trial's is reused.
        self.recheck_command = check_command(recheck_command) if recheck_command is not None else None
        # SSH (verifier_command) only triggers the external check; when a CAN channel is
        # configured, the pass/fail Device actually trusts arrives as an authenticated CAN
        # verdict instead, so a jammed or compromised SSH/Wi-Fi hop cannot forge the result.
        self.verifier_can_channel = verifier_can_channel
        self._verifier_can_secret = Path(verifier_can_secret_path).read_bytes() if verifier_can_secret_path else None
        self.reports = []
        self._checking_service = False
        self._switching = False

    speed_socket = "/run/capstone-cluster/model.sock"   # the cluster's own model: the speed it was told over CAN
    standstill_s = 2.0                                  # the vehicle must report 0 km/h this long before the install starts

    def vehicle_speed(self):
        """The speed the cluster currently shows (km/h), or None when it cannot be read."""
        from .qt_verify import socket_request
        try:
            report = socket_request(self.speed_socket, {"op": "snapshot", "nonce": "standstill"})
        except (OSError, ValueError):
            return None
        # Anything but a plain, finite number from a valid snapshot is "no reading" (a bool is not a speed: False == 0).
        speed = report.get("model_speed") if isinstance(report, dict) and report.get("valid") is True else None
        return speed if type(speed) in (int, float) and math.isfinite(speed) else None

    def _park(self, max_s, clock=time.monotonic, sleep=time.sleep):
        """Wait until the cluster reports a steady standstill (an update is installed standing still), at most `max_s`.
        A reading of "moving" restarts the time; no reading is never "stopped". Demo pacing `park` is the limit."""
        end, stopped_since = clock() + max_s, None
        while clock() < end:
            if self.vehicle_speed() == 0:
                stopped_since = clock() if stopped_since is None else stopped_since
                if clock() - stopped_since >= self.standstill_s:
                    return
            else:
                stopped_since = None
            sleep(0.5)

    def _check_arguments(self, app):
        if self._checking_service:
            return ['runuser', '-u', 'capstone-app', '--', str(app), '--check']
        return super()._check_arguments(app)

    def _service(self, action):
        # The applications of one image are one body: whenever this service is stopped (a switch, or the head unit
        # stopping after a refused recovery) the others stop with it. They are restarted and checked only during an
        # activation (which a repair also goes through), so a stopped image comes back together.
        together = action == "stop" or self._switching
        names = [self.service_name, *(self.companion_services if together else ())]
        results = [subprocess.run(["systemctl", action, name], timeout=10,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 for name in names]
        return all(results)

    def _activate(self, slot, state):
        # Initialize/start the selected target before asking an external observer.
        self._checking_service = state['systemd']
        self._switching = True
        try:
            super()._activate(slot, state)
        finally:
            self._checking_service = False
            self._switching = False
        if self.verifier_command is None:
            return
        recovering = state.get("committed") and slot == state["committed"]["slot"]
        stage = "recheck" if recovering else "verify"
        self._progress(stage, verified_slot=None, observation=None)
        nonce = can_verdict.new_nonce() if self.verifier_can_channel else None
        # Bind the CAN listener before triggering the remote check: a verdict frame
        # sent while nobody is bound yet to hear it is simply gone (CAN has no
        # replay), so listening has to start before the trigger goes out, not after.
        listener = can_verdict.open_listener(self.verifier_can_channel) if nonce is not None else None
        command = [*(self.recheck_command if recovering and self.recheck_command else self.verifier_command), str(self._slot(slot))]
        if nonce is not None:
            command += ["--nonce", nonce.hex()]
        try:
            try:
                rows, _pid = exchange(command, [], timeout=self.verifier_timeout,
                                      allow_negative_verdict=True)
                if (len(rows) != 1 or not isinstance(rows[0], dict)
                        or type(rows[0].get("passed")) is not bool):
                    raise ValueError("invalid external verification result")
                report = rows[0]
            except (OSError, ValueError) as exc:
                report = {"passed": False, "error": str(exc)}
            if nonce is not None:
                # The SSH round trip above only proves the remote check ran; it is no
                # longer trusted for the verdict itself. Keep what it said as a
                # diagnostic, but the authoritative result is the authenticated CAN frame.
                report["ssh_diagnostic_passed"] = report.get("passed")
                try:
                    report["passed"] = can_verdict.await_verdict(
                        listener, self._verifier_can_secret, nonce, timeout=self.verifier_timeout)
                    report.pop("error", None)
                except (OSError, ValueError, TimeoutError) as exc:
                    report = {"passed": False, "error": str(exc), "ssh_diagnostic_passed": report.get("ssh_diagnostic_passed")}
        finally:
            if listener is not None:
                listener.close()
        self.reports.append(report)
        observed = report.get("observed", [])
        if not isinstance(observed, list):
            observed = []
        rows = [row for row in observed if isinstance(row, dict)]
        if report["passed"] is not True:
            first = {}
            if not self._progress_data.get('mismatch'):
                mismatch = next((row for row in rows if row.get('passed') is False), None)
                if mismatch:
                    record = state['committed'] if recovering else state['pending']
                    first = {'mismatch': mismatch, 'mismatch_slot': slot,
                             'mismatch_version': record['release']['version'],
                             'mismatch_phase': 'recovery' if recovering else 'candidate'}
            fixture = report.get('attack_fixture', {})
            injected = fixture.get('old_version') if isinstance(fixture, dict) and fixture.get('old_signature_valid') is True else None
            self._progress("verification_failed", **first,
                           injected_recovery_version=self._progress_data.get('injected_recovery_version') or injected,
                           detail=str(report.get("error", "")))
            raise ValueError("Cluster external verification failed")
        self._progress(stage, verified_slot=slot, observation=rows[-1] if rows else None)
