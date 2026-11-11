"""Target-side application installation; no Central slots or existing Qt services."""
import subprocess
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
    verifier_timeout = 60                        # seconds an external verifier may take

    def __init__(self, root, verifier_command=None, recovery_verification=True,
                 verifier_can_channel=None, verifier_can_secret_path=None):
        super().__init__(root, recovery_verification=recovery_verification)
        self.verifier_command = check_command(verifier_command) if verifier_command is not None else None
        # SSH (verifier_command) only triggers the external check; when a CAN channel is
        # configured, the pass/fail Device actually trusts arrives as an authenticated CAN
        # verdict instead, so a jammed or compromised SSH/Wi-Fi hop cannot forge the result.
        self.verifier_can_channel = verifier_can_channel
        self._verifier_can_secret = Path(verifier_can_secret_path).read_bytes() if verifier_can_secret_path else None
        self.reports = []
        self._checking_service = False

    def _check_arguments(self, app):
        if self._checking_service:
            return ['runuser', '-u', 'capstone-app', '--', str(app), '--check']
        return super()._check_arguments(app)

    def _service(self, action):
        return subprocess.run(["systemctl", action, self.service_name], timeout=10,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def _activate(self, slot, state):
        # Initialize/start the selected target before asking an external observer.
        self._checking_service = state['systemd']
        try:
            super()._activate(slot, state)
        finally:
            self._checking_service = False
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
        command = [*self.verifier_command, str(self._slot(slot))]
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
