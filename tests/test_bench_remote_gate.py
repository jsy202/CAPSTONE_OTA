"""Trusted root bench forwards checks to the selected third-board verifier."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('verdict,expected', [({'passed': True, 'transport': 'SocketCAN directly observed by verifier'}, 0),
                                             ({'passed': 'true'}, 1), ({'passed': False}, 1)])
def test_checker_uses_selected_remote_verifier_and_preserves_source(tmp_path, verdict, expected):
    config = tmp_path / 'unused-peers.json'; config.write_text('{}')
    gate = tmp_path / 'gate.json'; gate.write_text(json.dumps({'host': 'verifier-pi', 'checkout': '/runtime',
                          'python': '/runtime/python', 'peers': '/verifier/peers.json'}))
    binary = tmp_path / 'bin'; binary.mkdir()
    observed = tmp_path / 'ssh-command'
    ssh = binary / 'ssh'
    ssh.write_text('#!' + sys.executable + '\nimport sys\nfrom pathlib import Path\n'
                   + 'Path(' + repr(str(observed)) + ').write_text(sys.argv[-1])\n'
                   + 'print(' + repr(json.dumps(verdict)) + ')\n')
    ssh.chmod(0o755)
    result = subprocess.run([sys.executable, '-m', 'bench.verify_service', '--config', str(config),
                            '--verifier', str(gate), '--capture-dir', str(tmp_path / 'captures'),
                            '--source', 'latest', '/slots/B'], capture_output=True, text=True,
                            env=dict(os.environ, PATH=str(binary)+':'+os.environ['PATH']), timeout=10)
    assert observed.exists(), result.stderr
    assert '--remote /verifier/peers.json --source latest /slots/B' in observed.read_text()
    assert result.returncode == expected, result.stdout + result.stderr
    assert type(json.loads(result.stdout)['passed']) is bool


@pytest.mark.parametrize('selected', [{'verifier_ssh': ['ssh', 'pi3']},
                                     {'verifier_peer': {'host': 'verifier-pi', 'checkout': '/runtime', 'python': '/runtime/python'}}])
def test_partial_third_board_selection_cannot_fall_back_to_local_observation(tmp_path, selected):
    from bench.physical import PhysicalBench
    arm = tmp_path / 'dashboard'
    header = bytearray(20); header[:5] = b'\x7fELF\x02'; header[18:20] = (183).to_bytes(2, 'little')
    arm.write_bytes(header)
    cfg = dict(central_ssh=['ssh', 'pi1'], cluster_ssh=['ssh', 'pi2'], runtime='/runtime',
               laptop_ip='127.0.0.1', arm_binary=str(arm), **selected)
    with pytest.raises(ValueError, match='third-board'):
        PhysicalBench(cfg, tmp_path / 'output')
