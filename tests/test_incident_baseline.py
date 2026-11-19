"""bench.incident_field: the baseline version is configurable (default unchanged)."""
import pytest

from bench import incident_field as field


def test_baseline_version_defaults_to_the_old_value_and_can_be_configured():
    assert field.baseline_version({}) == "2.0.0"
    assert field.baseline_version({"baseline_version": "2.1.0"}) == "2.1.0"


def test_the_incident_server_maps_the_configured_baseline_to_the_v2_release():
    assert field.release_labels({}) == {"2.0.0": "v2"}
    assert field.release_labels({"baseline_version": "2.1.0"}) == {"2.1.0": "v2"}


def test_the_attack_image_must_stay_older_than_the_baseline():
    assert field.attack_version("2.1.0") == "1.0.0"
    with pytest.raises(SystemExit, match="older"):
        field.attack_version("1.0.0")
    with pytest.raises(SystemExit, match="older"):
        field.attack_version("0.9.0")


def test_the_baseline_check_uses_the_configured_version():
    field.require_baseline({"version": "2.1.0", "phase": "stable"}, {"baseline_version": "2.1.0"})
    with pytest.raises(SystemExit, match="baseline"):
        field.require_baseline({"version": "2.0.0", "phase": "stable"}, {"baseline_version": "2.1.0"})
    with pytest.raises(SystemExit, match="baseline"):
        field.require_baseline({"version": "2.1.0", "phase": "rolled_back"}, {"baseline_version": "2.1.0"})
    field.require_baseline({"version": "2.0.0", "phase": "stable"}, {})  # default unchanged
