"""Send one REAL mouse click to an X display through the XTEST extension. Runs on the board (no packages needed:
libX11 and libXtst are already installed there). The target program (the cockpit's Qt app) receives an ordinary
pointer press/release, exactly as from a physical mouse.  Usage: python3 xclick.py DISPLAY X Y [BUTTON]"""
import ctypes
import re
import sys
import time


def parse(argv):
    if len(argv) not in (3, 4) or not re.fullmatch(r":\d{1,3}", argv[0]):
        raise SystemExit("usage: xclick.py :DISPLAY X Y [BUTTON]")
    try:
        x, y = int(argv[1]), int(argv[2])
        button = int(argv[3]) if len(argv) == 4 else 1
    except ValueError:
        raise SystemExit("X, Y and BUTTON must be integers") from None
    if x < 0 or y < 0 or not 1 <= button <= 5:
        raise SystemExit("X and Y must be >= 0 and BUTTON 1..5")
    return argv[0], x, y, button


def click(display, x, y, button=1, hold=0.08):
    x11, xtst = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    handle = x11.XOpenDisplay(display.encode())
    if not handle:
        raise SystemExit(f"cannot open display {display}")
    try:
        xtst.XTestFakeMotionEvent(handle, -1, x, y, 0)
        x11.XFlush(handle)
        time.sleep(0.15)                      # let the pointer-enter reach the window before the press
        xtst.XTestFakeButtonEvent(handle, button, 1, 0)
        x11.XFlush(handle)
        time.sleep(hold)
        xtst.XTestFakeButtonEvent(handle, button, 0, 0)
        x11.XFlush(handle)
        time.sleep(0.05)
    finally:
        x11.XCloseDisplay(handle)


if __name__ == "__main__":
    click(*parse(sys.argv[1:]))
