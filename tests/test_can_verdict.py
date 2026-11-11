"""Authenticated CAN verdict transport: happy path, forged secret, stale/foreign nonce."""
import socket
import threading

import pytest

from capstone_ota.can_verdict import new_nonce, receive_verdict, send_verdict

SECRET = b'test-shared-secret'


@pytest.fixture
def vcan():
    try:
        probe = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        probe.bind(('vcan0',))
        probe.close()
    except OSError:
        pytest.skip('vcan0 is not available in this environment')
    return 'vcan0'


def _send_after(delay, channel, secret, nonce, passed):
    timer = threading.Timer(delay, send_verdict, args=(channel, secret, nonce, passed))
    timer.start()
    return timer


def test_genuine_verdict_round_trips(vcan):
    nonce = new_nonce()
    timer = _send_after(0.1, vcan, SECRET, nonce, True)
    try:
        assert receive_verdict(vcan, SECRET, nonce, timeout=2) is True
    finally:
        timer.join()


def test_genuine_failure_round_trips(vcan):
    nonce = new_nonce()
    timer = _send_after(0.1, vcan, SECRET, nonce, False)
    try:
        assert receive_verdict(vcan, SECRET, nonce, timeout=2) is False
    finally:
        timer.join()


def test_wrong_secret_cannot_forge_a_verdict(vcan):
    nonce = new_nonce()
    timer = _send_after(0.1, vcan, b'wrong-secret', nonce, True)
    try:
        with pytest.raises(TimeoutError):
            receive_verdict(vcan, SECRET, nonce, timeout=1)
    finally:
        timer.join()


def test_stale_nonce_is_ignored(vcan):
    nonce = new_nonce()
    timer = _send_after(0.1, vcan, SECRET, new_nonce(), True)  # different round trip
    try:
        with pytest.raises(TimeoutError):
            receive_verdict(vcan, SECRET, nonce, timeout=1)
    finally:
        timer.join()


def test_no_verdict_at_all_times_out(vcan):
    with pytest.raises(TimeoutError):
        receive_verdict(vcan, SECRET, new_nonce(), timeout=0.3)
