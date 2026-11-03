"""Malformed burst must be rejected before even its valid first frame is sent."""
import json
import socket
import subprocess
import sys
import pytest
from capstone_ota.qt_source import frame


@pytest.mark.parametrize('messages', [[frame(620, 243), {'id': 512, 'data': '00'}],
                                     [frame(620, 243)] * 3])
def test_invalid_burst_sends_no_partial_traffic(messages):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', 0)); receiver.settimeout(.1)
        result = subprocess.run([sys.executable, '-m', 'capstone_ota.qt_source', '--udp-port',
                                str(receiver.getsockname()[1]), '--frames', json.dumps(messages)],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 1
        assert json.loads(result.stdout)['passed'] is False
        with pytest.raises(TimeoutError):
            receiver.recv(4096)
