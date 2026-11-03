"""Target-side application installation; no Central slots or existing Qt services."""
import subprocess

from .device import Device
from .process import exchange


def check_command(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(part, str) or not part or "\x00" in part for part in value)):
        raise ValueError("check command must be a nonempty JSON array of arguments")
    return list(value)


class ClusterDevice(Device):
    def __init__(self, root, verifier_command=None):
        super().__init__(root)
        self.verifier_command = check_command(verifier_command) if verifier_command is not None else None
        self.reports = []
        self._checking_service = False

    def _check_arguments(self, app):
        if self._checking_service:
            return ['runuser', '-u', 'capstone-app', '--', str(app), '--check']
        return super()._check_arguments(app)

    def _service(self, action):
        return subprocess.run(["systemctl", action, "capstone-cluster.service"], timeout=10,
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
        try:
            rows, _pid = exchange([*self.verifier_command, str(self._slot(slot))], [], timeout=60,
                                  allow_negative_verdict=True)
            if (len(rows) != 1 or not isinstance(rows[0], dict)
                    or type(rows[0].get("passed")) is not bool):
                raise ValueError("invalid external verification result")
            report = rows[0]
        except (OSError, ValueError) as exc:
            report = {"passed": False, "error": str(exc)}
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
