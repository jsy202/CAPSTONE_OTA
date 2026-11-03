import json
import os
from pathlib import Path
import time


def test_mirror_reads_remote_status_and_keeps_last_timestamp_on_disconnect(tmp_path, monkeypatch):
    from dashboard.qt_progress.remote import Mirror
    binary = tmp_path / 'bin'; binary.mkdir()
    response = tmp_path / 'response.json'
    response.write_text(json.dumps({'progress': {'stage': 'verify', 'pid': 999999, 'updated': 1},
                                    'alive': True, 'device': '/opt/capstone-cluster/runs/test/device', 'host': 'pi2'}))
    ssh = binary / 'ssh'
    ssh.write_text('#!/bin/sh\ncat '+str(response)+'\n');ssh.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+':'+os.environ['PATH'])
    m = Mirror({'cluster_ssh':['ssh','pi2'],'runtime':'/runtime'}, tmp_path / 'mirror')
    m.sample()
    good = json.loads((tmp_path/'mirror/progress.json').read_text())
    assert good['pid'] == 999999
    assert good['_remote']['writer_alive'] is True
    assert good['_remote']['received'] > time.time()-3
    ssh.write_text('#!/bin/sh\nexit 255\n')
    m.sample()
    stale = json.loads((tmp_path/'mirror/progress.json').read_text())
    assert stale['_remote']['received'] == good['_remote']['received']
    assert stale['_remote']['error']


def test_mirror_rejects_non_ssh_configuration(tmp_path):
    import pytest
    from dashboard.qt_progress.remote import Mirror
    with pytest.raises(ValueError, match='ssh'):
        Mirror({'cluster_ssh':['sh','-c','true'],'runtime':'/runtime'},tmp_path/'mirror')
