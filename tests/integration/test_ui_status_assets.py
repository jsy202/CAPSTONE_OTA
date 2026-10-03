"""Interface/configuration verification of the Cluster UI status deployment assets."""
import re
from pathlib import Path


OTA = Path(__file__).parents[2] / "ota"
HELPER = "/opt/capstone-ota/venv/bin/capstone-ota-ui-status"
# The snapshot needs only the journal owner's rights; '+' is used solely to
# leave the app's own sandbox, then setpriv drops root before the helper runs.
DROP = "/usr/bin/setpriv --reuid=capstone-ota --regid=capstone-ota --init-groups --no-new-privs --"
ARGS = ("--install-root /opt/digital-cluster --initial-version 1.0.0 "
        "--output /run/capstone-ota-ui/digital-cluster.json")


def _lines(name):
    return [line.strip() for line in (OTA / name).read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_cluster_app_snapshots_status_without_blocking_or_widening_access():
    lines = _lines("systemd/digital-cluster.service")
    assert f"ExecStartPre=-+{DROP} {HELPER} {ARGS}" in lines
    assert "ExecStart=/opt/digital-cluster/active-slot/bin/digital-dash" in lines
    assert "User=digital-dash" in lines
    assert not any(line.startswith("ReadWritePaths") for line in lines)
    assert not any(line.startswith("ExecStartPre=") and DROP not in line for line in lines)
    assert lines.index(f"ExecStartPre=-+{DROP} {HELPER} {ARGS}") < lines.index(
        "ExecStart=/opt/digital-cluster/active-slot/bin/digital-dash")


def test_watch_service_is_unprivileged_and_hardened():
    lines = _lines("systemd/capstone-ota-ui-status.service")
    assert f"ExecStart={HELPER} {ARGS} --watch --interval 1.0" in lines
    for expected in ("User=capstone-ota", "Group=capstone-ota", "ProtectSystem=strict",
                     "ReadWritePaths=/run/capstone-ota-ui", "NoNewPrivileges=true",
                     "RestrictAddressFamilies=AF_UNIX", "Restart=on-failure",
                     "WantedBy=multi-user.target", "ProtectHome=true", "PrivateTmp=true"):
        assert expected in lines, expected
    assert not any(line.startswith(("ExecStart=+", "ExecStart=!")) for line in lines)


def test_status_initial_version_matches_zone_agent():
    def versions(unit):
        return re.findall(r"--initial-version (\S+)", "\n".join(_lines(unit)))
    expected = versions("systemd/capstone-ota-zone-agent.service")
    assert len(expected) == 1
    for unit in ("systemd/digital-cluster.service", "systemd/capstone-ota-ui-status.service"):
        assert versions(unit) == expected


def test_tmpfiles_owns_runtime_directory():
    assert _lines("tmpfiles/capstone-ota-ui.conf") == ["d /run/capstone-ota-ui 0755 capstone-ota capstone-ota -"]
