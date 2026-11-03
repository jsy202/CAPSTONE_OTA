"""Mirror root-owned Cluster telemetry over existing SSH; never command an update."""
import argparse
import base64
from collections import deque
import os
import re
import signal
import threading
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
packet={'progress':data,'alive':alive,'writer_age':time.time()-data['updated'],'host':os.uname().nodename,'device':p,'logs':[]}
try:
 fd=os.open(str(Path(p)/'events.jsonl'),os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as f:
  info=os.fstat(f.fileno())
  if not stat.S_ISREG(info.st_mode):raise ValueError('invalid events file')
  offset=max(0,info.st_size-16384);f.seek(offset);lines=f.read(16384).splitlines()
  if offset:lines=lines[1:]
  for line in lines[-12:]:
   try:
    row=json.loads(line);packet['logs'].append(time.strftime('%H:%M:%S',time.localtime(row['time']))+'  '+str(row['event'])[:120])
   except (ValueError,KeyError,TypeError,OverflowError):pass
except OSError:pass
'''
SCREEN_PROBE = r'''
import base64,subprocess,secrets
from capstone_ota.qt_verify import socket_request
try:
 nonce=secrets.token_hex(8)
 model=socket_request('/run/capstone-cluster/model.sock',{'op':'snapshot','nonce':nonce})
 if not isinstance(model,dict) or not isinstance(model.get('executable'),str):raise ValueError('invalid screen identity')
 if model.get('nonce')!=nonce or model.get('ui_ready') is not True or Path(model.get('executable','')).resolve()!=(Path(p)/'current/libexec/dashboard').resolve():
  raise ValueError('selected Cluster screen is not ready')
 command=['runuser','-u','capstone-app','--','env','DISPLAY=:93','XAUTHORITY=/var/lib/capstone-app/Xauthority',
          'timeout','--kill-after=1s','2s','ffmpeg','-nostdin','-loglevel','error','-f','x11grab','-video_size','1280x480','-i',':93',
          '-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','-']
 image=subprocess.run(command,capture_output=True,timeout=4)
 if image.returncode or not 8<=len(image.stdout)<=5000000 or not image.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
  raise ValueError('Cluster screen capture unavailable')
 after=socket_request('/run/capstone-cluster/model.sock',{'op':'snapshot','nonce':nonce})
 if after.get('pid')!=model.get('pid') or after.get('executable')!=model.get('executable') or Path('/opt/capstone-cluster/selected-device').read_text().strip()!=p:
  raise ValueError('Cluster changed during screen capture')
 packet['image']=base64.b64encode(image.stdout).decode();packet['screen_error']=''
except (OSError,ValueError,subprocess.SubprocessError) as error:
 packet['screen_error']=str(error)[:160]
'''


def ssh_arguments(argv):
    if (not isinstance(argv,list) or not argv or argv[0] not in {'ssh','/usr/bin/ssh'}
            or any(not isinstance(x,str) or not x or '\x00' in x for x in argv)):
        raise ValueError('monitor requires explicit trusted ssh arguments')
    return list(argv)


class CanTail:
    """Bounded read-only candump sessions; a stopped session renews on the next poll."""
    def __init__(self,arguments,source="CAN"):
        self.arguments=ssh_arguments(arguments);self.source=source
        self.process=self.thread=None
        self.lines=deque(maxlen=10);self.received=0;self.error='';self.lock=threading.Lock()

    def ensure_started(self):
        if self.process is not None:
            if self.process.poll() is None or self.thread.is_alive():return
            self.process.stdout.close()
        self.process=subprocess.Popen([*self.arguments,'timeout 60 candump -L can0,200:7FF'],
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        self.thread=threading.Thread(target=self._read,args=(self.process,),daemon=True);self.thread.start()

    def _read(self,process):
        for line in iter(lambda:process.stdout.readline(512),''):
            line=line.strip()
            with self.lock:
                if re.fullmatch(r'\([0-9.]+\)\s+\w+\s+200#[0-9a-fA-F]{16}',line):
                    self.lines.append(line);self.received=time.time();self.error=''
                elif line:self.error=line[:160]

    def snapshot(self):
        with self.lock:return {'lines':list(self.lines),'received':self.received,'error':self.error,'source':self.source}

    def close(self):
        if self.process:
            if self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait()
            self.thread.join(timeout=2)
            if not self.thread.is_alive():self.process.stdout.close()



class Mirror:
    def __init__(self, config, output, presentation=False):
        argv = ssh_arguments(config.get('cluster_ssh'))
        runtime = config.get('runtime')
        self.presentation=presentation;self.can_tail=None
        if not isinstance(runtime, str) or not runtime.startswith('/') or '\x00' in runtime:
            raise ValueError('mirror runtime must be an absolute path')
        self.command = [*argv, shlex.join(['sudo', '-n', runtime+'/venv/bin/python', '-c', PROBE+(SCREEN_PROBE if presentation else '')+'\nprint(json.dumps(packet))'])]
        self.output = Path(output).resolve(); self.output.mkdir(parents=True, exist_ok=False)
        self.last, self.fingerprint = {}, None

    def sample(self):
        try:
            result = subprocess.run(self.command, capture_output=True, timeout=5)
            if result.returncode or len(result.stdout)>(8000000 if self.presentation else 131072):
                raise ValueError('remote telemetry unavailable (ssh exit '+str(result.returncode)+')')
            packet = json.loads(result.stdout)
            data = packet['progress']
            if not isinstance(data,dict) or type(packet['alive']) is not bool:
                raise ValueError('invalid remote progress envelope')
            data['_remote'] = {'received':time.time(), 'writer_alive':packet['alive'],
                               'writer_age':packet.get('writer_age',0), 'source':packet['host'],
                               'device':packet['device'], 'error':''}
            data['_logs']=[str(line)[:240] for line in packet.get('logs',[])][-12:]
            if self.presentation:
                screen={'available':False,'received':self.last.get('_screen',{}).get('received',0),'error':packet.get('screen_error','screen unavailable')}
                try:
                    image=base64.b64decode(packet.get('image',''),validate=True)
                    if not 8<=len(image)<=5000000 or not image.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('invalid preview image')
                    atomic_write(self.output/'dashboard.png',image)
                    screen.update(available=True,received=time.time(),revision=str(time.monotonic_ns()),error='')
                except (ValueError,TypeError):pass
                data['_screen']=screen
            self.last = data
            fingerprint = json.dumps({k:v for k,v in data.items() if k not in {'pid','updated','_remote','_screen','_can'}},sort_keys=True)+packet['device']
            if fingerprint != self.fingerprint:
                with (self.output/'observations.jsonl').open('a') as f:
                    f.write(json.dumps(data,ensure_ascii=False)+'\n')
                self.fingerprint = fingerprint
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            meta = self.last.setdefault('_remote',{'received':0,'writer_alive':False})
            meta['error'] = str(exc)[:200]  # Keep the last receipt time; errors never refresh evidence.
        if self.can_tail:
            self.can_tail.ensure_started();self.last['_can']=self.can_tail.snapshot()
        atomic_write(self.output/'progress.json',json.dumps(self.last).encode())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hardware-config',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--no-window',action='store_true')
    p.add_argument('--presentation',action='store_true',help='Actual Cluster screen, monitor and logs in one window')
    p.add_argument('--seconds',type=float,default=0)
    p.add_argument('--x');p.add_argument('--y',default='0')
    args=p.parse_args()
    if args.seconds<0:p.error('--seconds must be nonnegative')
    config=json.loads(args.hardware_config.read_text())
    mirror=Mirror(config,args.output,presentation=args.presentation)
    if args.presentation:mirror.can_tail=CanTail(config.get('verifier_ssh',config['cluster_ssh']),'Pi3' if 'verifier_ssh' in config else 'Pi2')
    viewer=None
    try:
        mirror.sample()
        if not args.no_window:
            command=[str(ROOT/'dashboard/qt_progress/run'),'--root',str(mirror.output),'--x',args.x or ('0' if args.presentation else '1280'),'--y',args.y]
            if args.presentation:command+=['--dashboard-image',str(mirror.output/'dashboard.png')]
            viewer=subprocess.Popen(command)
        end=time.monotonic()+args.seconds if args.seconds else float('inf')
        while time.monotonic()<end and (viewer is None or viewer.poll() is None):
            mirror.sample();time.sleep(.5)
        if viewer is not None and viewer.poll() not in (None,0):
            raise SystemExit(viewer.returncode)
    except KeyboardInterrupt:
        pass
    finally:
        if mirror.can_tail:mirror.can_tail.close()
        if viewer and viewer.poll() is None:
            viewer.terminate()
            try:viewer.wait(timeout=3)
            except subprocess.TimeoutExpired:viewer.kill();viewer.wait()


if __name__=='__main__':
    main()
