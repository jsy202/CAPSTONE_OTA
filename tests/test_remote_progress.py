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


def test_presentation_preview_failure_does_not_reuse_old_image_as_live(tmp_path,monkeypatch):
    import base64
    from dashboard.qt_progress.remote import Mirror
    binary=tmp_path/'bin';binary.mkdir();response=tmp_path/'packet'
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6b1kAAAAASUVORK5CYII=')
    packet={'progress':{'stage':'committed','updated':1,'pid':1},'alive':False,'device':'/opt/capstone-cluster/runs/test/device','host':'pi2','logs':['12:00:00 trial'],'image':base64.b64encode(png).decode(),'screen_error':''}
    response.write_text(json.dumps(packet));ssh=binary/'ssh';ssh.write_text('#!/bin/sh\ncat '+str(response)+'\n');ssh.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+':'+os.environ['PATH'])
    m=Mirror({'cluster_ssh':['ssh','pi2'],'runtime':'/runtime'},tmp_path/'mirror',presentation=True)
    m.sample();good=json.loads((m.output/'progress.json').read_text())
    assert good['_screen']['available'] is True and (m.output/'dashboard.png').read_bytes()==png
    assert good['_logs']==['12:00:00 trial']
    packet.pop('image');packet['screen_error']='application restarting';response.write_text(json.dumps(packet))
    m.sample();bad=json.loads((m.output/'progress.json').read_text())
    assert bad['_screen']['available'] is False
    assert bad['_remote']['error']==''  # Telemetry still works independently.
    assert bad['_screen']['received']==good['_screen']['received']


def test_can_reader_stops_only_its_owned_capture_process(tmp_path,monkeypatch):
    import time
    from dashboard.qt_progress.remote import CanTail
    binary=tmp_path/'bin';binary.mkdir();ssh=binary/'ssh'
    ssh.write_text('#!/bin/sh\nprintf "(1.000000) can0 200#0011223344556677\\n"\nexec sleep 60\n');ssh.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+':'+os.environ['PATH'])
    tail=CanTail(['ssh','pi3']);tail.ensure_started()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and not tail.snapshot()['lines']:time.sleep(.02)
    assert tail.snapshot()['lines']==['(1.000000) can0 200#0011223344556677']
    process=tail.process;tail.close()
    assert process.poll() is not None


def test_hanging_screen_capture_does_not_leave_its_child_running(tmp_path,monkeypatch):
    import signal,sys
    import capstone_ota.qt_verify as qt
    from dashboard.qt_progress.remote import SCREEN_PROBE
    binary=tmp_path/'bin';binary.mkdir();pidfile=tmp_path/'child.pid'
    user=binary/'runuser';user.write_text('#!/bin/sh\nshift 3\nexec "$@"\n');user.chmod(0o755)
    ffmpeg=binary/'ffmpeg'
    ffmpeg.write_text('#!'+sys.executable+'\nimport subprocess\nfrom pathlib import Path\np=subprocess.Popen(["sleep","60"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nPath('+repr(str(pidfile))+').write_text(str(p.pid))\np.wait()\n');ffmpeg.chmod(0o755)
    monkeypatch.setenv('PATH',str(binary)+':'+os.environ['PATH'])
    monkeypatch.setattr(qt,'socket_request',lambda path,req:{'nonce':req['nonce'],'ui_ready':True,'pid':123,'executable':str(tmp_path/'current/libexec/dashboard')})
    context={'Path':Path,'p':str(tmp_path),'packet':{}}
    try:
        exec(SCREEN_PROBE,context)
        assert context['packet']['screen_error']
        pid=int(pidfile.read_text())
        status=Path('/proc/'+str(pid)+'/stat')
        assert not status.exists() or status.read_text().split()[2]=='Z','screen capture left a running child'
    finally:
        if pidfile.exists():
            try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
            except ProcessLookupError:pass
