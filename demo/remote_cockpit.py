"""Read-only live view of Pi2's rendered cockpit, transported over pinned SSH."""
import json
import shlex
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAME = b''
AT = 0.0
REMOTE = '''import io,struct,sys,time
from pathlib import Path
from PIL import Image
shot=Path('/tmp/cockpit-shot.png')
while True:
    Path('/run/cockpit/shot.request').touch()
    time.sleep(0.65)
    try:
        with Image.open(shot) as im:
            im=im.convert('RGB'); im.thumbnail((1920,1280))
            out=io.BytesIO(); im.save(out,format='JPEG',quality=78)
        data=out.getvalue()
        sys.stdout.buffer.write(struct.pack('!I',len(data))+data); sys.stdout.buffer.flush()
    except (OSError,ValueError): pass
    time.sleep(0.2)
'''
SSH = ['ssh', '-F', '/home/jsy/.ssh/capstone-pi2.conf', '-oProxyJump=none',
       '-oHostName=172.20.10.3', '-oConnectTimeout=5', '-oServerAliveInterval=5',
       '-oServerAliveCountMax=2', 'capstone-pi2']


def receive():
    global FRAME, AT
    while True:
        with subprocess.Popen(SSH + ['sudo -n /usr/bin/python3 -u -c ' + shlex.quote(REMOTE)],
                              stdout=subprocess.PIPE) as proc:
            try:
                while True:
                    size = proc.stdout.read(4)
                    if len(size) != 4: break
                    size = struct.unpack('!I', size)[0]
                    if not 0 < size < 8_000_000: break
                    data = bytearray()
                    while len(data) < size:
                        part = proc.stdout.read(size-len(data))
                        if not part: raise EOFError()
                        data.extend(part)
                    FRAME, AT = bytes(data), time.time()
            except (OSError, EOFError): pass
            finally:
                proc.terminate()
                proc.wait(timeout=10)
        time.sleep(2)


class View(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/frame'):
            data, kind = FRAME, 'image/jpeg'
        elif self.path.startswith('/status'):
            data, kind = json.dumps({'age': round(time.time()-AT, 1) if AT else None}).encode(), 'application/json'
        else:
            kind = 'text/html; charset=utf-8'
            data = '''<!doctype html><meta charset="utf-8"><title>Pi2 · CAPSTONE 원격 화면</title>
<style>html,body{margin:0;background:#05070a;color:#ddd;font:14px sans-serif;height:100%;overflow:hidden}
header{height:32px;display:flex;align-items:center;justify-content:space-between;padding:0 16px}
img{display:block;width:100%;height:calc(100% - 32px);object-fit:contain}span{color:#ff7a1a}</style>
<header>Pi2 · 실제 콕핏 화면 / SSH <span id="state">연결 중</span></header><img id="screen">
<script>const img=document.querySelector('#screen'); async function tick(){
try {const s=await(await fetch('/status')).json(); document.querySelector('#state').textContent=
s.age===null?'연결 중':s.age>4?'화면 갱신 중단 · '+s.age+'초':'LIVE · 약 1fps';
if(s.age!==null)img.src='/frame?t='+Date.now();}catch(e){}
setTimeout(tick,700);}tick();</script>'''.encode()
        self.send_response(200 if data else 503)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_): pass


if __name__ == '__main__':
    threading.Thread(target=receive, daemon=True).start()
    print('Pi2 remote view: http://127.0.0.1:8766', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), View).serve_forever()
