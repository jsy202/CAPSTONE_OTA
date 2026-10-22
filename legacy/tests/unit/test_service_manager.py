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

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    clock = Clock()
    manager = SystemdServiceManager(runner=runner, clock=clock.now, sleeper=clock.sleep)

    assert manager.restart_and_wait_healthy("digital-dash.service", 15) is True
    assert calls[0][0] == ["systemctl", "restart", "digital-dash.service"]
    assert all(call[0] == ["systemctl", "is-active", "--quiet", "digital-dash.service"] for call in calls[1:])
    assert len(calls) > 2
    assert clock.value == 15
    assert all(call[1]["timeout"] == 10 for call in calls)


def test_systemd_manager_rejects_service_that_crashes_during_stability_window():
    active_checks = iter([0, 0, 3])

    def runner(command, **_kwargs):
        if command[1] == "restart":
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=next(active_checks))

    clock = Clock()
    manager = SystemdServiceManager(
        runner=runner, clock=clock.now, sleeper=clock.sleep, poll_interval=1
    )
    assert manager.restart_and_wait_healthy("digital-dash.service", 15) is False
    assert clock.value == 2


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


def test_systemd_manager_accepts_explicit_units_and_freezes_caller_allowlist():
    calls = []
    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)
    clock = Clock()
    allowed = {"central-control.service", "digital-cluster.service"}
    manager = SystemdServiceManager(
        runner=runner, clock=clock.now, sleeper=clock.sleep, allowed_units=allowed
    )
    allowed.add("attacker.service")
    allowed.remove("central-control.service")
    assert manager.restart_and_wait_healthy("central-control.service", 1)
    assert calls[0] == ["systemctl", "restart", "central-control.service"]
    assert all(command == ["systemctl", "is-active", "--quiet", "central-control.service"] for command in calls[1:])
    with pytest.raises(ValueError):
        manager.restart_and_wait_healthy("attacker.service", 1)
    with pytest.raises(ValueError):
        manager.restart_and_wait_healthy("digital-dash.service", 1)


@pytest.mark.parametrize("unit", ["--all.service", "bad unit.service", "../evil.service", "bad\n.service", "no-suffix"])
def test_systemd_manager_rejects_unsafe_allowed_unit_configuration(unit):
    with pytest.raises(ValueError):
        SystemdServiceManager(allowed_units={unit})
