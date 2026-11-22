import ast
import io
import json
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from bench import remote_view as rv


def get(url, data=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def serve(store, click=None):
    srv = rv.make_server(store, port=0, click=click)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_frame_round_trip_and_truncation():
    blob = b"\xff\xd8abc\xff\xd9"
    stream = io.BytesIO(rv.pack_frame(blob) + rv.pack_frame(b"x"))
    assert list(rv.read_frames(stream)) == [blob, b"x"]
    assert list(rv.read_frames(io.BytesIO(struct.pack("!I", 10) + b"abc"))) == []
    assert list(rv.read_frames(io.BytesIO(struct.pack("!I", 0)))) == []
    assert list(rv.read_frames(io.BytesIO(struct.pack("!I", 1 << 30)))) == []


def test_map_click():
    assert rv.map_click(0, 0) == (0, 0)
    assert rv.map_click(1, 1) == (2159, 1439)
    assert rv.map_click(0.5, 0.5) == (1080, 720)
    for bad in [(-0.1, 0.5), (0.5, 1.1), (float("nan"), 0.5), ("a", 0.5), (None, 0)]:
        with pytest.raises(ValueError):
            rv.map_click(*bad)


def test_parse_button():
    assert rv.parse_button("left") == 1 and rv.parse_button(3) == 3 and rv.parse_button(None) == 1
    with pytest.raises(ValueError):
        rv.parse_button("laser")


def test_ssh_validation():
    assert rv.validate_ssh(["ssh", "-p", "22", "host"])
    for bad in (["scp", "x"], [], "ssh host", None):
        with pytest.raises(ValueError):
            rv.validate_ssh(bad)


def test_grabber_script_is_safe_python():
    src = rv.grabber_script(fps=2)
    compile(src, "<grabber>", "exec")
    allowed = {"io", "struct", "sys", "time", "PIL"}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert {a.name.split(".")[0] for a in node.names} <= allowed
        if isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in allowed
    assert "xdisplay" in src and "sleep" in src
    compile(rv.CLICK_SCRIPT, "<click>", "exec")
    with pytest.raises(ValueError):
        rv.grabber_script(fps=0)


def test_click_command_shape():
    cmd = rv.click_command(1080, 720, 1)
    assert cmd.startswith("DISPLAY=:94 python3 -c ") and cmd.endswith(" 1080 720 1")


def test_endpoints_and_stale():
    store = rv.FrameStore()
    srv, base = serve(store)
    try:
        status, _, body = get(base + "/")
        assert status == 200 and b"/frame?ts=" in body and b"no signal" in body
        assert get(base + "/frame")[0] == 503
        store.put(b"\xff\xd8jpeg")
        status, headers, body = get(base + "/frame?ts=1")
        assert (status, body) == (200, b"\xff\xd8jpeg") and headers["Content-Type"] == "image/jpeg"
        assert json.loads(get(base + "/status")[2])["stale"] is False
        store.put(b"old", at=time.monotonic() - 10)
        assert json.loads(get(base + "/status")[2])["stale"] is True
        assert get(base + "/frame")[0] == 503
    finally:
        srv.shutdown()
        srv.server_close()


def test_click_disabled_by_default():
    srv, base = serve(rv.FrameStore())
    try:
        assert get(base + "/click", b'{"x":0.5,"y":0.5}')[0] == 403
        assert b"/click" not in get(base + "/")[2]
    finally:
        srv.shutdown()
        srv.server_close()


def test_click_enabled():
    clicks = []
    srv, base = serve(rv.FrameStore(), click=lambda x, y, b: clicks.append((x, y, b)))
    try:
        assert get(base + "/click", json.dumps({"x": 0.5, "y": 0.25, "button": "right"}).encode())[0] == 200
        assert clicks == [(1080, 360, 3)]
        assert get(base + "/click", b'{"x":2,"y":0}')[0] == 400
        assert get(base + "/click", b"not json")[0] == 400
        assert len(clicks) == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_reader_with_fake_ssh(tmp_path):
    # A fake "ssh" that ignores the command and emits two framed frames, then exits.
    fake = tmp_path / "ssh"
    fake.write_text(
        f"#!{sys.executable}\nimport sys,struct\nfor d in (b'aa', b'bbb'):\n"
        "    sys.stdout.buffer.write(struct.pack('!I',len(d))+d)\nsys.stdout.buffer.flush()\n")
    fake.chmod(0o755)
    store = rv.FrameStore()
    reader = rv.Reader([str(fake)], "pass", store, retry=0.05, validate=False)
    reader.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and store.get()[0] != b"bbb":
            time.sleep(0.02)
        assert store.get()[0] == b"bbb"
    finally:
        reader.stop()


@pytest.mark.skipif(not shutil.which("Xvfb"), reason="Xvfb not installed")
def test_grabber_against_local_xvfb():
    pytest.importorskip("PIL")
    xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "800x600x24", "-nolisten", "tcp"],
                            stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        src = rv.grabber_script(fps=5, display=":97")
        p = subprocess.Popen([sys.executable, "-u", "-c", src], stdout=subprocess.PIPE)
        frames = []
        for f in rv.read_frames(p.stdout):
            frames.append(f)
            if len(frames) == 2:
                break
        p.stdout.close()  # grabber must notice and exit
        assert p.wait(timeout=5) is not None
        assert len(frames) == 2 and frames[0][:2] == b"\xff\xd8"
    finally:
        xvfb.terminate()
        xvfb.wait()
