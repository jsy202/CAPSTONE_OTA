"""bench.incident_field: what Pi1 sends must be exactly what Pi2's pinned gate allows (no board needed)."""
import shlex

from bench import incident_field as field
from capstone_ota import ota_gate
from capstone_ota.incident_responder import IncidentResponder
from capstone_ota.relay import target_command


def _remote(argv):
    return "cd " + shlex.quote(field.RUNTIME) + " && " + shlex.join([*argv, "http://" + field.PI1_ETH + ":4321"])


def test_the_gate_allows_the_update_pi1_sends():
    trial = field.forced_failure(25)
    config = field.gate_config(trial, field.passing_recheck())
    assert ota_gate.allowed(_remote(target_command(field.target_for(trial))), config) is not None


def test_the_gate_allows_the_repair_pi1_sends():
    trial = field.forced_failure(25)
    config = field.gate_config(trial, field.passing_recheck())
    responder = IncidentResponder.__new__(IncidentResponder)
    responder.target = field.target_for(trial)  # the update's check differs from the repair's re-check
    ssh = responder._repair_command("http://" + field.PI1_ETH + ":4321")
    assert ota_gate.allowed(ssh[-1], config) is not None


def test_the_gate_refuses_an_update_with_another_check():
    config = field.gate_config(field.forced_failure(25), field.passing_recheck())
    other = field.forced_failure(1)
    assert ota_gate.allowed(_remote(target_command(field.target_for(other))), config) is None
