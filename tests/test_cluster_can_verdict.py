"""The external verifier's pass/fail must be authenticated over CAN, not trusted from SSH/stdout."""
import socket
import sys

import pytest

from capstone_ota import cluster
from capstone_ota.can_verdict import VERDICT_CAN_ID
from test_device import keys, payload, releases, serving  # noqa: F401


@pytest.fixture
def vcan():
    try:
        probe = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        probe.bind(('vcan0',))
        probe.close()
    except OSError:
        pytest.skip('vcan0 is not available in this environment')
    return 'vcan0'


def _secret(tmp_path, content=b'shared-test-secret'):
    path = tmp_path / 'can-verdict.key'
    path.write_bytes(content)
    return path


def test_can_verdict_overrides_a_forged_stdout_pass(releases, keys, tmp_path, vcan):
    """A compromised/jammed SSH hop prints passed:true, but never posts a valid CAN
    verdict. Device must not trust the stdout value and must not commit the update."""
    secret = _secret(tmp_path)
    script = tmp_path / 'fake_verifier.py'
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'passed': True}))\n"  # forged success on the untrusted leg
        "raise SystemExit(0)\n"
    )
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, str(script)],
                                   verifier_can_channel=vcan, verifier_can_secret_path=secret)
    with serving(releases) as url:
        result = device.update(url + '/v2', keys[1])
    assert result['result'] == 'failed'
    assert result['version'] is None
    assert device.reports[-1]['passed'] is False
    assert device.reports[-1]['ssh_diagnostic_passed'] is True


def test_can_verdict_accepts_a_genuine_authenticated_pass(releases, keys, tmp_path, vcan):
    secret = _secret(tmp_path)
    script = tmp_path / 'fake_verifier.py'
    script.write_text(
        "import json, sys\n"
        "from capstone_ota.can_verdict import send_verdict\n"
        "nonce = bytes.fromhex(sys.argv[sys.argv.index('--nonce') + 1])\n"
        f"send_verdict('vcan0', open({str(secret)!r}, 'rb').read(), nonce, True)\n"
        "print(json.dumps({'passed': True}))\n"
    )
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, str(script)],
                                   verifier_can_channel=vcan, verifier_can_secret_path=secret)
    with serving(releases) as url:
        result = device.update(url + '/v2', keys[1])
    assert result['result'] == 'committed'
    assert device.reports[-1]['passed'] is True


def test_can_verdict_signed_with_the_wrong_secret_cannot_force_a_commit(releases, keys, tmp_path, vcan):
    """An attacker who can write to the CAN bus but does not know the shared secret
    (e.g. one of the two boards' secret files, not both) cannot forge a PASS."""
    secret = _secret(tmp_path)
    script = tmp_path / 'fake_verifier.py'
    script.write_text(
        "import json, sys\n"
        "from capstone_ota.can_verdict import send_verdict\n"
        "nonce = bytes.fromhex(sys.argv[sys.argv.index('--nonce') + 1])\n"
        "send_verdict('vcan0', b'attacker-does-not-know-this-secret', nonce, True)\n"
        "print(json.dumps({'passed': True}))\n"
    )
    device = cluster.ClusterDevice(tmp_path / 'cluster', [sys.executable, str(script)],
                                   verifier_can_channel=vcan, verifier_can_secret_path=secret)
    device.verifier_timeout = 2  # keep the forced timeout fast
    with serving(releases) as url:
        result = device.update(url + '/v2', keys[1])
    assert result['result'] == 'failed'
    assert device.reports[-1]['passed'] is False
