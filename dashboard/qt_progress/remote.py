"""Mirror root-owned Cluster telemetry over existing SSH; never command an update."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

from capstone_ota.device import atomic_write

ROOT = Path(__file__).resolve().parents[2]
PROBE = r'''
import json,os,stat,time
from pathlib import Path
p=Path('/opt/capstone-cluster/selected-device').read_text().strip()
if not p.startswith('/opt/capstone-cluster/runs/') or Path(p).name!='device':
 raise ValueError('selected device is outside bench roots')
fd=os.open(str(Path(p)/'progress.json'),os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as f:
 info=os.fstat(f.fileno())
 if not stat.S_ISREG(info.st_mode) or not 1<=info.st_size<=65536:raise ValueError('invalid progress file')
 data=json.loads(f.read(65537))
if not isinstance(data,dict):raise ValueError('invalid progress object')
pid=data.get('pid',0);alive=False
if type(pid)==int and pid>0:
 try:os.kill(pid,0);alive=True
 except ProcessLookupError:pass
print(json.dumps({'progress':data,'alive':alive,'writer_age':time.time()-data['updated'],'host':os.uname().nodename,'device':p}))
'''


class Mirror:
    def __init__(self, config, output):
        argv = config.get('cluster_ssh')
        runtime = config.get('runtime')
        if (not isinstance(argv, list) or not argv or argv[0] not in {'ssh', '/usr/bin/ssh'}
                or any(not isinstance(x, str) or not x or '\x00' in x for x in argv)):
            raise ValueError('mirror requires explicit trusted ssh arguments')
        if not isinstance(runtime, str) or not runtime.startswith('/') or '\x00' in runtime:
            raise ValueError('mirror runtime must be an absolute path')
        self.command = [*argv, shlex.join(['sudo', '-n', runtime+'/venv/bin/python', '-c', PROBE])]
        self.output = Path(output).resolve(); self.output.mkdir(parents=True, exist_ok=False)
        self.last, self.fingerprint = {}, None

    def sample(self):
        try:
            result = subprocess.run(self.command, capture_output=True, timeout=5)
            if result.returncode or len(result.stdout)>131072:
                raise ValueError('remote telemetry unavailable (ssh exit '+str(result.returncode)+')')
            packet = json.loads(result.stdout)
            data = packet['progress']
            if not isinstance(data,dict) or type(packet['alive']) is not bool:
                raise ValueError('invalid remote progress envelope')
            data['_remote'] = {'received':time.time(), 'writer_alive':packet['alive'],
                               'writer_age':packet.get('writer_age',0), 'source':packet['host'],
                               'device':packet['device'], 'error':''}
            self.last = data
            fingerprint = json.dumps({k:v for k,v in data.items() if k not in {'pid','updated','_remote'}},sort_keys=True)+packet['device']
            if fingerprint != self.fingerprint:
                with (self.output/'observations.jsonl').open('a') as f:
                    f.write(json.dumps(data,ensure_ascii=False)+'\n')
                self.fingerprint = fingerprint
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            meta = self.last.setdefault('_remote',{'received':0,'writer_alive':False})
            meta['error'] = str(exc)[:200]  # Keep the last receipt time; errors never refresh evidence.
        atomic_write(self.output/'progress.json',json.dumps(self.last).encode())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hardware-config',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--no-window',action='store_true')
    p.add_argument('--seconds',type=float,default=0)
    p.add_argument('--x',default='1280');p.add_argument('--y',default='0')
    args=p.parse_args()
    if args.seconds<0:p.error('--seconds must be nonnegative')
    mirror=Mirror(json.loads(args.hardware_config.read_text()),args.output)
    viewer=None
    try:
        mirror.sample()
        if not args.no_window:
            viewer=subprocess.Popen([str(ROOT/'dashboard/qt_progress/run'),'--root',str(mirror.output),'--x',args.x,'--y',args.y])
        end=time.monotonic()+args.seconds if args.seconds else float('inf')
        while time.monotonic()<end and (viewer is None or viewer.poll() is None):
            mirror.sample();time.sleep(.5)
        if viewer is not None and viewer.poll() not in (None,0):
            raise SystemExit(viewer.returncode)
    except KeyboardInterrupt:
        pass
    finally:
        if viewer and viewer.poll() is None:
            viewer.terminate()
            try:viewer.wait(timeout=3)
            except subprocess.TimeoutExpired:viewer.kill();viewer.wait()


if __name__=='__main__':
    main()
