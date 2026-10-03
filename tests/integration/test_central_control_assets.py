"""Deployment contract (A8) of the Central Control application units."""
from pathlib import Path

OTA = Path(__file__).parents[2] / "ota"
DROP = "/usr/bin/setpriv --reuid=capstone-ota --regid=capstone-ota --init-groups --no-new-privs --"
HELPER = "/opt/capstone-ota/venv/bin/capstone-ota-ui-status"
ARGS = ("--install-root /opt/central-control --initial-version 1.0.0 "
        "--output /run/capstone-ota-ui/central-control.json")


def _lines(name):
    return [l.strip() for l in (OTA / name).read_text().splitlines() if l.strip() and not l.strip().startswith("#")]


def test_central_app_runs_unprivileged_with_identity_snapshot():
    lines = _lines("systemd/central-control.service")
    assert f"ExecStartPre=-+{DROP} {HELPER} {ARGS}" in lines
    assert "ExecStart=/opt/central-control/active-slot/bin/central-control" in lines
    for expected in ("User=central-control", "Group=central-control", "RuntimeDirectory=central-control",
                     "RuntimeDirectoryMode=0750", "RestrictAddressFamilies=AF_UNIX AF_CAN",
                     "NoNewPrivileges=true", "ProtectSystem=strict", "PrivateTmp=true"):
        assert expected in lines, expected
    assert not any(l.startswith("ReadWritePaths") for l in lines)


def test_central_status_watcher_is_unprivileged_and_hardened():
    lines = _lines("systemd/capstone-ota-central-status.service")
    assert f"ExecStart={HELPER} {ARGS} --watch --interval 1.0" in lines
    for expected in ("User=capstone-ota", "ProtectSystem=strict", "ReadWritePaths=/run/capstone-ota-ui",
                     "NoNewPrivileges=true", "RestrictAddressFamilies=AF_UNIX", "Restart=on-failure"):
        assert expected in lines, expected


def test_coordinator_can_reach_central_socket_by_group_only():
    coordinator = _lines("systemd/capstone-ota-coordinator.service")
    assert "SupplementaryGroups=central-control" in coordinator
