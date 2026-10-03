#!/usr/bin/env python3
"""Desktop system-level visual check of the patched dashboard (not hardware).

Runs the real patched VolvoDigitalDashModels binary on an Xvfb display, feeds
the status file through the demo sequence and saves one PNG per state:

  1-stable-1.0.0  2-trial-1.1.1  3-restored-1.0.0  4-stable-1.0.0  5-corrupt
  and, with --layouts N, the trial badge on N upstream layouts (screen-NN-*).

Each capture also measures the clearance between upstream content and the
badge plate; an overlap fails the run unless it is listed as a reviewed
decorative overlap.

usage: capture_dashboard_states.py --app BIN --qt QT_PREFIX --out DIR
                                   [--extra-lib-dir DIR] [--display :99]

Exit status is non-zero when the application exits early (crash) at any step.
Screens are evidence for a human reviewer; pixel colors of the badge corner
are also summarized in DIR/capture.json for an automated sanity check.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path


def status(state: str, version: str | None, restored_at: float | None = None) -> str:
    return json.dumps({"schema_version": 1, "state": state, "version": version, "restored_at": restored_at})


def read_xwd(raw: bytes) -> tuple[int, int, bytes]:
    """Decode a 24/32-bit ZPixmap XWD (as written by `xwd -root`) to RGB rows."""
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


PLATE_WIDTH, PLATE_HEIGHT, PLATE_MARGIN = 124, 50, 6
# upstream main.qml advanceScreen(): screen index -> layout file
SCREENS = ["BigTachCenter", "BigTachLeft", "Original240Layout", "Original740Layout",
           "Original240LayoutClock", "Original850R", "OriginalRSportLayout", "Original544Layout",
           "OriginalP1800Layout", "OriginalEarly240Layout", "Original140RallyeLayout"]
# Overlaps reviewed by a human on the captured PNG: decorative only, no
# indicator, lamp, gauge or value is covered. Reported as REVIEWED, not PASS.
REVIEWED_DECORATIVE = {"OriginalEarly240Layout": "panel frame line passes under the badge corner"}


def badge_clearance(width: int, height: int, rgb: bytes) -> dict:
    """Measure the gap between the last upstream lamp and the badge plate.

    Scans each row of the plate band leftwards from the plate's left edge and
    records the right-most non-background pixel of upstream content. Overlap
    means upstream content touches the plate area; a gap of 2+ px passes.
    """
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


class Keyboard:
    """Send Right-arrow presses through XTest (no xdotool needed)."""
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


def capture(display: str, window: str, out: Path) -> tuple[int, int, bytes]:
    raw = subprocess.run(["xwd", "-silent", "-display", display, "-name", window],
                         check=True, capture_output=True).stdout
    width, height, rgb = read_xwd(raw)
    write_png(out, width, height, rgb)
    return width, height, rgb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--qt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extra-lib-dir", type=Path, action="append", default=[])
    parser.add_argument("--display", default=":99")
    parser.add_argument("--layouts", type=int, default=0,
                        help="after the state sequence, also capture N layouts in trial state")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    status_file = args.out / "status.json"
    status_file.write_text(status("stable", "1.0.0"))

    xvfb = subprocess.Popen(["Xvfb", args.display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    env = dict(os.environ, DISPLAY=args.display, CAPSTONE_APP_STATUS_FILE=str(status_file),
               LD_LIBRARY_PATH=":".join(str(p) for p in [args.qt / "lib", *args.extra_lib_dir]))
    log = (args.out / "app.log").open("w")
    app = subprocess.Popen([str(args.app)], env=env, stdout=log, stderr=subprocess.STDOUT)
    steps = [
        ("1-stable-1.0.0", status("stable", "1.0.0")),
        ("2-trial-1.1.1", status("trial", "1.1.1")),
        ("3-restored-1.0.0", None),  # restored_at is written just before capture
        ("4-stable-1.0.0", status("stable", "1.0.0", 1.0)),  # an old restore mark
        ("5-corrupt", "{corrupt"),
    ]
    summary, failed = [], False
    try:
        time.sleep(5.0)  # upstream boot screen lasts 2 s after the first layout load
        keyboard = Keyboard(args.display)  # raises and focuses the main window
        for name, content in steps:
            status_file.write_text(content if content is not None else status("stable", "1.0.0", time.time()))
            time.sleep(2.5)  # > one 1000 ms AppStatus poll
            if app.poll() is not None:
                failed = True
                summary.append({"step": name, "error": f"application exited with {app.returncode}"})
                break
            width, height, rgb = capture(args.display, "Lolvo", args.out / f"{name}.png")
            summary.append({"step": name, "screen": "Original240Layout", "png": f"{name}.png",
                            "size": [width, height], **badge_clearance(width, height, rgb)})
            failed = failed or summary[-1]["overlap"]
        if not failed and args.layouts:
            status_file.write_text(status("trial", "1.1.1"))
            screen = 0  # upstream starts on Original240Layout with screen index 0
            for _ in range(args.layouts):
                keyboard.right()
                screen = (screen + 1) % len(SCREENS)
                name = f"screen-{screen:02d}-{SCREENS[screen]}"
                time.sleep(3.0)
                if app.poll() is not None:
                    failed = True
                    summary.append({"step": name, "error": f"exited {app.returncode}"})
                    break
                width, height, rgb = capture(args.display, "Lolvo", args.out / f"{name}.png")
                result = {"step": name, "screen": SCREENS[screen], "png": f"{name}.png",
                          **badge_clearance(width, height, rgb)}
                if result["overlap"] and SCREENS[screen] in REVIEWED_DECORATIVE:
                    result["verdict"] = "REVIEWED: " + REVIEWED_DECORATIVE[SCREENS[screen]]
                else:
                    result["verdict"] = "FAIL" if result["overlap"] else "PASS"
                    failed = failed or result["overlap"]
                summary.append(result)
    finally:
        app.terminate()
        try:
            app.wait(5)
        except subprocess.TimeoutExpired:
            app.kill()
        xvfb.terminate()
        log.close()
    (args.out / "capture.json").write_text(json.dumps({"failed": failed, "steps": summary}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
