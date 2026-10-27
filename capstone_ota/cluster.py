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

    def _service(self, action):
        return subprocess.run(["systemctl", action, "capstone-cluster.service"], timeout=10,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def _activate(self, slot, state):
        # Initialize/start the selected target before asking an external observer.
        super()._activate(slot, state)
        if self.verifier_command is None:
            return
        try:
            rows, _pid = exchange([*self.verifier_command, str(self._slot(slot))], [], timeout=15,
                                  allow_negative_verdict=True)
            if (len(rows) != 1 or not isinstance(rows[0], dict)
                    or type(rows[0].get("passed")) is not bool):
                raise ValueError("invalid external verification result")
            report = rows[0]
        except (OSError, ValueError) as exc:
            report = {"passed": False, "error": str(exc)}
        self.reports.append(report)
        if report["passed"] is not True:
            raise ValueError("Cluster external verification failed")
