from types import SimpleNamespace

import pytest

from capstone_ota.agent.service_manager import SystemdServiceManager


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_systemd_manager_uses_fixed_commands_and_reports_healthy():
    calls = []
    results = iter([SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)])

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return next(results)

    clock = Clock()
    manager = SystemdServiceManager(runner=runner, clock=clock.now, sleeper=clock.sleep)

    assert manager.restart_and_wait_healthy("digital-dash.service", 15) is True
    assert [call[0] for call in calls] == [
        ["systemctl", "restart", "digital-dash.service"],
        ["systemctl", "is-active", "--quiet", "digital-dash.service"],
    ]
    assert all(call[1]["timeout"] == 10 for call in calls)


def test_systemd_manager_times_out_when_service_never_becomes_active():
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0 if command[1] == "restart" else 3)

    clock = Clock()
    manager = SystemdServiceManager(
        runner=runner, clock=clock.now, sleeper=clock.sleep, poll_interval=1
    )

    assert manager.restart_and_wait_healthy("digital-dash.service", 3) is False
    assert clock.value == 3


def test_systemd_manager_rejects_mqtt_controlled_unit_name():
    manager = SystemdServiceManager(runner=lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError):
        manager.restart_and_wait_healthy("attacker.service", 15)
