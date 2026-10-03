#!/usr/bin/env python3
"""Desktop system-level visual check of the patched dashboard (not hardware).

Runs the real patched VolvoDigitalDashModels binary on an Xvfb display, feeds
the status file through the demo sequence and saves one PNG per state:

  1-stable-1.0.0  2-trial-1.1.1  3-restored-1.0.0  4-stable-1.0.0  5-corrupt

With --layouts N it also cycles N upstream layouts in the trial state
(screen-NN-<layout>.png). With --baseline-app it repeats the layout cycle
with the unpatched upstream binary and counts upstream pixels that lie under
the badge plate or the 4 px frame lines: content the badge would cover.

Two automated checks per layout:
  * clearance: no bright upstream content touches the plate's left edge
  * coverage (baseline): no upstream content inside the plate rectangle
A violation fails the run unless the layout is a reviewed decorative overlap.
capture.json records SHA-256 of the badge sources so stale evidence is caught.

usage: capture_dashboard_states.py --app BIN --qt QT_PREFIX --out DIR
           [--baseline-app BIN] [--layouts N] [--extra-lib-dir DIR] [--display :99]
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
OVERLAY = HERE.parent / "overlay" / "app"
PLATE_WIDTH, PLATE_HEIGHT, PLATE_MARGIN, STRIP = 124, 50, 6, 4
# upstream main.qml advanceScreen(): screen index -> layout file
SCREENS = ["BigTachCenter", "BigTachLeft", "Original240Layout", "Original740Layout",
           "Original240LayoutClock", "Original850R", "OriginalRSportLayout", "Original544Layout",
           "OriginalP1800Layout", "OriginalEarly240Layout", "Original140RallyeLayout"]
# Overlaps reviewed by a human on the captured PNG: decorative only, no
# indicator, lamp, gauge or value is covered. Reported as REVIEWED, not PASS.
REVIEWED_DECORATIVE = {"OriginalEarly240Layout": "panel frame line passes under the badge corner"}


def status(state: str, version: str | None, restored_at: float | None = None) -> str:
    return json.dumps({"schema_version": 1, "state": state, "version": version, "restored_at": restored_at})


def source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in (("StatusBadge.qml", OVERLAY / "StatusBadge.qml"),
                               ("app_status.cpp", OVERLAY / "src" / "capstone" / "app_status.cpp"))}


def read_xwd(raw: bytes) -> tuple[int, int, bytes]:
    """Decode a 32-bit-per-pixel ZPixmap XWD (LSB first) to RGB rows."""
    header = struct.unpack(">25I", raw[:100])
    header_size, width, height = header[0], header[4], header[5]
    bytes_per_line, ncolors, byte_order = header[12], header[19], header[7]
    # Xvfb may report 24 bits per pixel while storing 4 bytes per pixel.
    if header[2] != 2 or bytes_per_line < width * 4 or byte_order != 0:
        raise ValueError("unsupported XWD format")
    offset = header_size + ncolors * 12
    rgb = bytearray()
    for y in range(height):
        row = raw[offset + y * bytes_per_line: offset + y * bytes_per_line + width * 4]
        for x in range(width):
            b, g, r = row[4 * x], row[4 * x + 1], row[4 * x + 2]
            rgb += bytes((r, g, b))
    return width, height, bytes(rgb)


def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def _crop(width: int, rgb: bytes, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, bytes]:
    return x1 - x0, y1 - y0, b"".join(rgb[(y * width + x0) * 3:(y * width + x1) * 3] for y in range(y0, y1))


def badge_clearance(width: int, height: int, rgb: bytes) -> dict:
    """Gap between the right-most bright upstream pixel and the plate's left edge."""
    left = width - PLATE_MARGIN - PLATE_WIDTH
    top, bottom = height - PLATE_MARGIN - PLATE_HEIGHT, height - PLATE_MARGIN
    content_right = -1
    for y in range(top, bottom):
        row = rgb[y * width * 3:(y + 1) * width * 3]
        for x in range(left - 1, left - 200, -1):
            r, g, b = row[3 * x:3 * x + 3]
            if max(r, g, b) > 40:
                content_right = max(content_right, x)
                break
    return {"plate_left": left, "content_right_edge": content_right,
            "gap_px": left - 1 - content_right, "overlap": content_right >= left - 2}


def covered_content(width: int, height: int, rgb: bytes) -> dict:
    """On a badge-less baseline frame, count upstream pixels the badge would cover.

    The threshold is low (any channel > 16) so dim, unlit lamps count too.
    """
    def lit(x: int, y: int) -> bool:
        i = (y * width + x) * 3
        return max(rgb[i], rgb[i + 1], rgb[i + 2]) > 16
    left, right = width - PLATE_MARGIN - PLATE_WIDTH, width - PLATE_MARGIN
    top, bottom = height - PLATE_MARGIN - PLATE_HEIGHT, height - PLATE_MARGIN
    plate = sum(lit(x, y) for y in range(top, bottom) for x in range(left, right))
    strips = sum(lit(x, y) for y in [*range(STRIP), *range(height - STRIP, height)] for x in range(width))
    return {"plate_px": plate, "strip_px": strips}


class Keyboard:
    """Focus the main window and send Right-arrow presses through XTest."""
    XK_RIGHT = 0xFF53

    def __init__(self, display: str):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.dpy = ctypes.c_void_p(self.x11.XOpenDisplay(display.encode()))
        if not self.dpy:
            raise RuntimeError("cannot open X display")
        # No window manager runs on Xvfb: give the main window focus directly.
        info = subprocess.run(["xwininfo", "-display", display, "-name", "Lolvo"],
                              check=True, capture_output=True, text=True).stdout
        window = int(info.split("Window id: ")[1].split()[0], 16)
        self.x11.XSetInputFocus(self.dpy, ctypes.c_ulong(window), 2, 0)  # RevertToParent, CurrentTime
        self.x11.XRaiseWindow(self.dpy, ctypes.c_ulong(window))
        self.x11.XFlush(self.dpy)
        time.sleep(0.5)

    def right(self) -> None:
        code = self.x11.XKeysymToKeycode(self.dpy, ctypes.c_ulong(self.XK_RIGHT))
        for down in (1, 0):
            self.xtst.XTestFakeKeyEvent(self.dpy, code, down, 0)
            self.x11.XFlush(self.dpy)
            time.sleep(0.05)


def capture(display: str, out: Path) -> tuple[int, int, bytes]:
    raw = subprocess.run(["xwd", "-silent", "-display", display, "-name", "Lolvo"],
                         check=True, capture_output=True).stdout
    width, height, rgb = read_xwd(raw)
    write_png(out, width, height, rgb)
    return width, height, rgb


def save_corner(width: int, height: int, rgb: bytes, out: Path) -> None:
    write_png(out, *_crop(width, rgb, width - 320, height - 140, width, height))


class Session:
    """One dashboard process on its own Xvfb display."""

    def __init__(self, app: Path, env: dict, display: str, log: Path):
        self.display = display
        self.xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        self.log = log.open("w")
        self.app = subprocess.Popen([str(app)], env=dict(env, DISPLAY=display),
                                    stdout=self.log, stderr=subprocess.STDOUT)
        time.sleep(5.0)  # upstream boot screen lasts 2 s after the first layout load
        self.keyboard = Keyboard(display)

    def alive(self) -> bool:
        return self.app.poll() is None

    def close(self) -> None:
        self.app.terminate()
        try:
            self.app.wait(5)
        except subprocess.TimeoutExpired:
            self.app.kill()
        self.xvfb.terminate()
        self.log.close()


def cycle_layouts(session: Session, count: int, out: Path, prefix: str):
    """Yield (screen name, frame) for count Right presses from the start screen."""
    screen = 0  # upstream starts on Original240Layout with screen index 0
    for _ in range(count):
        session.keyboard.right()
        screen = (screen + 1) % len(SCREENS)
        time.sleep(3.0)
        if not session.alive():
            yield SCREENS[screen], None
            return
        frame = capture(session.display, out / f"{prefix}screen-{screen:02d}-{SCREENS[screen]}.png")
        yield SCREENS[screen], frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--baseline-app", type=Path, help="unpatched upstream build for coverage")
    parser.add_argument("--qt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extra-lib-dir", type=Path, action="append", default=[])
    parser.add_argument("--display", default=":99")
    parser.add_argument("--layouts", type=int, default=0)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "corners").mkdir(exist_ok=True)
    status_file = args.out / "status.json"
    status_file.write_text(status("stable", "1.0.0"))
    env = dict(os.environ, CAPSTONE_APP_STATUS_FILE=str(status_file),
               LD_LIBRARY_PATH=":".join(str(p) for p in [args.qt / "lib", *args.extra_lib_dir]))

    steps = [
        ("1-stable-1.0.0", status("stable", "1.0.0")),
        ("2-trial-1.1.1", status("trial", "1.1.1")),
        ("3-restored-1.0.0", None),  # restored_at is written just before capture
        ("4-stable-1.0.0", status("stable", "1.0.0", 1.0)),  # an old restore mark
        ("5-corrupt", "{corrupt"),
    ]
    summary, failed = [], False
    session = Session(args.app, env, args.display, args.out / "app.log")
    try:
        for name, content in steps:
            status_file.write_text(content if content is not None else status("stable", "1.0.0", time.time()))
            time.sleep(2.5)  # > one 1000 ms AppStatus poll
            if not session.alive():
                failed = True
                summary.append({"step": name, "error": f"application exited with {session.app.returncode}"})
                break
            width, height, rgb = capture(args.display, args.out / f"{name}.png")
            save_corner(width, height, rgb, args.out / "corners" / f"{name}.png")
            summary.append({"step": name, "screen": "Original240Layout", **badge_clearance(width, height, rgb)})
            failed = failed or summary[-1]["overlap"]
        if not failed and args.layouts:
            status_file.write_text(status("trial", "1.1.1"))
            for screen, frame in cycle_layouts(session, args.layouts, args.out, ""):
                if frame is None:
                    failed = True
                    summary.append({"step": screen, "error": "application exited"})
                    break
                save_corner(*frame, args.out / "corners" / f"patched-{screen}.png")
                summary.append({"step": f"layout {screen}", "screen": screen, **badge_clearance(*frame)})
    finally:
        session.close()

    baseline = []
    if args.baseline_app and args.layouts and not failed:
        base = Session(args.baseline_app, env, args.display, args.out / "baseline.log")
        try:
            for screen, frame in cycle_layouts(base, args.layouts, args.out, "baseline-"):
                if frame is None:
                    failed = True
                    baseline.append({"screen": screen, "error": "baseline application exited"})
                    break
                save_corner(*frame, args.out / "corners" / f"baseline-{screen}.png")
                baseline.append({"screen": screen, **covered_content(*frame)})
        finally:
            base.close()

    covered = {b["screen"]: b for b in baseline if "plate_px" in b}
    for item in summary:
        screen = item.get("screen")
        if screen is None or "error" in item:
            continue
        coverage = covered.get(screen, {})
        item.update({f"baseline_{k}": v for k, v in coverage.items() if k != "screen"})
        violation = item["overlap"] or coverage.get("plate_px", 0) > 0
        if violation and screen in REVIEWED_DECORATIVE:
            item["verdict"] = "REVIEWED: " + REVIEWED_DECORATIVE[screen]
        else:
            item["verdict"] = "FAIL" if violation else "PASS"
            failed = failed or violation
    result = {"failed": failed, "source_sha256": source_hashes(),
              "baseline_compared": bool(baseline), "steps": summary, "baseline": baseline}
    (args.out / "capture.json").write_text(json.dumps(result, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
