"""Live window onto the Pi2 cockpit, which renders on a private virtual X display (Xvfb :94).

Board side: a stdlib+Pillow grabber streams length-prefixed JPEGs over ssh stdout.
Laptop side: a loopback-only HTTP page shows the newest frame. Clicking is OFF unless --allow-click.
"""
import argparse
import json
import math
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DISPLAY = ":94"
SCREEN_W, SCREEN_H = 2160, 1440
STALE_AFTER = 3.0
MAX_FRAME = 16_000_000

# Runs on the board: `python3 -u -c <this>`. Constants are prepended by grabber_script().
_GRABBER_BODY = '''
import io, struct, sys, time
from PIL import ImageGrab
interval = 1.0 / FPS
next_at = time.monotonic()
while True:
    try:
        im = ImageGrab.grab(xdisplay=DISPLAY).convert("RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, max(1, im.height * MAX_W // im.width)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=QUALITY)
        data = buf.getvalue()
        sys.stdout.buffer.write(struct.pack("!I", len(data)) + data)
        sys.stdout.buffer.flush()
    except (BrokenPipeError, ValueError):
        break  # stdout closed: the laptop went away
    except Exception:
        pass  # display not ready yet; try again on the next tick
    next_at += interval
    now = time.monotonic()
    if next_at < now:
        next_at = now + interval  # encode overran the slot: drop frames instead of catching up
    time.sleep(next_at - now)
'''

# Runs on the board with DISPLAY set: XTest click through ctypes (no xdotool needed). argv: X Y BUTTON
CLICK_SCRIPT = '''
import ctypes, sys
x, y, b = (int(v) for v in sys.argv[1:4])
X = ctypes.CDLL("libX11.so.6")
T = ctypes.CDLL("libXtst.so.6")
X.XOpenDisplay.restype = ctypes.c_void_p
X.XOpenDisplay.argtypes = [ctypes.c_char_p]
X.XFlush.argtypes = [ctypes.c_void_p]
T.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
T.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
d = X.XOpenDisplay(None)
if not d:
    sys.exit("cannot open display")
T.XTestFakeMotionEvent(d, 0, x, y, 0)
T.XTestFakeButtonEvent(d, b, 1, 0)
T.XTestFakeButtonEvent(d, b, 0, 0)
X.XFlush(d)
'''


def grabber_script(fps=4.0, max_width=1440, quality=70, display=DISPLAY):
    if not 0 < float(fps) <= 30:
        raise ValueError("fps must be in (0, 30]")
    head = f"FPS = {float(fps)!r}\nMAX_W = {int(max_width)}\nQUALITY = {int(quality)}\nDISPLAY = {display!r}\n"
    return head + _GRABBER_BODY


def pack_frame(data):
    return struct.pack("!I", len(data)) + data


def read_frames(stream):
    """Yield frames until EOF, a truncated frame or an absurd length (then stop: the stream is out of sync)."""
    while True:
        head = stream.read(4)
        if len(head) != 4:
            return
        size = struct.unpack("!I", head)[0]
        if not 0 < size <= MAX_FRAME:
            return
        data = stream.read(size)
        if len(data) != size:
            return
        yield data


def map_click(fx, fy, width=SCREEN_W, height=SCREEN_H):
    """Screenshot-relative fractions 0..1 -> pixel coordinates on the board display."""
    for v in (fx, fy):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError("click fractions must be numbers in 0..1")
    return min(width - 1, int(fx * width)), min(height - 1, int(fy * height))


def parse_button(value):
    names = {"left": 1, "middle": 2, "right": 3, None: 1}
    if isinstance(value, str) or value is None:
        if value in names:
            return names[value]
    elif isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3:
        return value
    raise ValueError("button must be left/middle/right or 1..3")


def validate_ssh(ssh):
    if not isinstance(ssh, list) or not ssh or ssh[0] not in {"ssh", "/usr/bin/ssh"}:
        raise ValueError("a board must be an explicit ssh argument list")
    return ssh


def click_command(x, y, button):
    return f"DISPLAY={DISPLAY} python3 -c {shlex.quote(CLICK_SCRIPT)} {int(x)} {int(y)} {int(button)}"


class FrameStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data, self._at = None, None

    def put(self, data, at=None):
        with self._lock:
            self._data, self._at = data, time.monotonic() if at is None else at

    def get(self):
        with self._lock:
            return self._data, self._at

    def age(self):
        _, at = self.get()
        return None if at is None else time.monotonic() - at


class Reader(threading.Thread):
    """Keeps one ssh grabber alive; reconnects `retry` seconds after it drops."""

    def __init__(self, ssh, script, store, retry=2.0, validate=True):
        super().__init__(daemon=True)
        self.ssh = validate_ssh(ssh) if validate else ssh
        self.script, self.store, self.retry = script, store, retry
        self._stop_event = threading.Event()
        self._proc = None

    def run(self):
        cmd = [*self.ssh, "python3 -u -c " + shlex.quote(self.script)]
        while not self._stop_event.is_set():
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
                for frame in read_frames(self._proc.stdout):
                    self.store.put(frame)
            except OSError:
                pass
            finally:
                self._kill()
            self._stop_event.wait(self.retry)

    def _kill(self):
        proc = self._proc
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.stdout:
            proc.stdout.close()

    def stop(self):
        self._stop_event.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


PAGE = """<!doctype html><meta charset="utf-8"><title>Pi2 screen</title>
<style>html,body{margin:0;height:100%;background:#05070a;overflow:hidden}
img{width:100%;height:100%;object-fit:contain;display:block}
#n{position:fixed;inset:0;display:none;align-items:center;justify-content:center;color:#889;font:20px sans-serif}</style>
<img id="i" alt=""><div id="n">no signal</div>
<script>
const i=document.getElementById('i'),n=document.getElementById('n');
async function tick(){
  try{const s=await(await fetch('/status',{cache:'no-store'})).json();
    if(s.stale){n.style.display='flex';i.style.visibility='hidden';}
    else{n.style.display='none';i.style.visibility='visible';i.src='/frame?ts='+Date.now();}
  }catch(e){n.style.display='flex';}
  setTimeout(tick,200);}
tick();
CLICK_HOOK
</script>"""

# Fractions are relative to the letterboxed image (object-fit: contain), not the element box.
CLICK_JS = """i.addEventListener('click',e=>{const r=i.getBoundingClientRect(),ar=%d/%d;
  let w=r.width,h=r.height,ox=0,oy=0;if(w/h>ar){const nw=h*ar;ox=(w-nw)/2;w=nw}else{const nh=w/ar;oy=(h-nh)/2;h=nh}
  const x=(e.clientX-r.left-ox)/w,y=(e.clientY-r.top-oy)/h;
  if(x>=0&&x<=1&&y>=0&&y<=1)fetch('/click',{method:'POST',body:JSON.stringify({x,y,button:'left'})});});"""


def make_server(store, port=8766, click=None):
    """`click(x, y, button)` is called for POST /click; None keeps clicking disabled (403)."""
    page = PAGE.replace("CLICK_HOOK", CLICK_JS % (SCREEN_W, SCREEN_H) if click else "").encode()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", kind="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/frame":
                data, _ = store.get()
                age = store.age()
                if data is None or age > STALE_AFTER:
                    self._send(503, b"no signal")
                else:
                    self._send(200, data, "image/jpeg")
            elif path == "/status":
                age = store.age()
                body = {"age": None if age is None else round(age, 2), "stale": age is None or age > STALE_AFTER}
                self._send(200, json.dumps(body).encode(), "application/json")
            else:
                self._send(404)

        def do_POST(self):
            if self.path.split("?")[0] != "/click":
                return self._send(404)
            if click is None:
                return self._send(403, b"click disabled (start with --allow-click)")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length < 4096:
                    raise ValueError("bad body size")
                req = json.loads(self.rfile.read(length))
                x, y = map_click(req["x"], req["y"])
                button = parse_button(req.get("button"))
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return self._send(400, str(e).encode())
            try:
                click(x, y, button)
            except Exception as e:  # report ssh failures to the caller, keep serving
                return self._send(502, str(e).encode())
            self._send(200, b"ok")

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def ssh_click(ssh):
    validate_ssh(ssh)

    def do(x, y, button):
        done = subprocess.run([*ssh, click_command(x, y, button)], capture_output=True, text=True, timeout=15)
        if done.returncode:
            raise RuntimeError(f"click failed: {done.stderr[-300:]}")
    return do


def open_window(url):
    for browser in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(browser):
            argv = [browser, f"--app={url}", "--window-size=1100,760"]
            break
    else:
        argv = ["xdg-open", url]
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bench.remote_view", description=__doc__)
    ap.add_argument("config")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--fps", type=float, default=4.0)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--allow-click", action="store_true")
    args = ap.parse_args(argv)
    from bench.incident_field import load
    _, boards = load(args.config)
    ssh = validate_ssh(boards["pi2"].ssh)
    store = FrameStore()
    reader = Reader(ssh, grabber_script(args.fps), store)
    reader.start()
    server = make_server(store, args.port, ssh_click(ssh) if args.allow_click else None)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Pi2 remote view: {url}  (click {'ON' if args.allow_click else 'off'})", flush=True)
    if args.open:
        open_window(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
