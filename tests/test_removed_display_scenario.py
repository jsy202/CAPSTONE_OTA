"""The retired replay demonstration must not be silently selected by acceptance checks."""
import pytest
from capstone_ota.qt_verify import verify


def test_retired_buffered_profile_is_rejected_before_accessing_a_target():
    with pytest.raises(ValueError, match='source profile'):
        verify({}, source='buffered')


def test_removed_mixed_demo_is_rejected_without_creating_output(tmp_path):
    from demo.ivi_attack import run
    output = tmp_path / 'retired'
    with pytest.raises(ValueError, match='scenario'):
        run(output, scenario='mixed')
    assert not output.exists()


def test_fixed_cluster_payload_has_no_version_or_receiver_policy(tmp_path):
    from dashboard.qt_cluster.build import payload
    binary = tmp_path / 'binary'; binary.write_bytes(b'fixed cluster')
    slot = payload(binary, tmp_path / 'payload')
    assert not (slot / 'cluster.json').exists()
    assert (slot / 'libexec/dashboard').read_bytes() == b'fixed cluster'
