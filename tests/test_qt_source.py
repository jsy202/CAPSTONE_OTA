"""Malformed vehicle input must be rejected before sending any traffic."""
import json
import socket
import subprocess
import sys
import pytest
from capstone_ota.qt_source import frame


@pytest.mark.parametrize('message', [{'id': 512, 'data': '00'}, [frame(620, 243)]])
def test_invalid_frame_sends_no_traffic(message):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', 0)); receiver.settimeout(.1)
        result = subprocess.run([sys.executable, '-m', 'capstone_ota.qt_source', '--udp-port',
                                str(receiver.getsockname()[1]), '--frame', json.dumps(message)],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 1
        assert json.loads(result.stdout)['passed'] is False
        with pytest.raises(TimeoutError):
            receiver.recv(4096)
