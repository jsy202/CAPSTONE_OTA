"""Send one REAL mouse click to the monitor that Pi2's HDMI cockpit runs on, through a virtual pointer (Linux uinput).
Pi2 has no mouse or touch panel, so nobody can tap the map tile. The compositor treats this virtual pointer exactly like
a physical mouse. Standard library only; needs root (/dev/uinput). Usage: sudo python3 uinput_click.py X Y"""
import fcntl
import os
import struct
import sys
import time

WIDTH, HEIGHT = 2160, 1440            # the monitor's mode (the cockpit is laid out for 2160x1440)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN_LEFT, ABS_X, ABS_Y = 0x110, 0, 1


def parse(argv):
    if len(argv) != 2:
        raise SystemExit("usage: uinput_click.py X Y")
    try:
        x, y = int(argv[0]), int(argv[1])
    except ValueError:
        raise SystemExit("X and Y must be integers") from None
    if not (0 <= x < WIDTH and 0 <= y < HEIGHT):
        raise SystemExit(f"X must be 0..{WIDTH - 1} and Y 0..{HEIGHT - 1}")
    return x, y


def device_description():
    """struct uinput_user_dev: name[80], id (bustype, vendor, product, version), ff_effects_max, absmax/min/fuzz/flat[64]."""
    absmax = [0] * 64
    absmax[ABS_X], absmax[ABS_Y] = WIDTH - 1, HEIGHT - 1
    return (struct.pack("80sHHHHi", b"capstone-virtual-pointer", 3, 1, 1, 1, 0)
            + struct.pack("64i", *absmax) + struct.pack("64i", *[0] * 64) * 3)


def event(kind, code, value):
    return struct.pack("llHHi", 0, 0, kind, code, value)


def click(x, y, hold=0.12):
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        for kind in (EV_KEY, EV_ABS, EV_SYN):
            fcntl.ioctl(fd, UI_SET_EVBIT, kind)
        fcntl.ioctl(fd, UI_SET_KEYBIT, BTN_LEFT)
        for axis in (ABS_X, ABS_Y):
            fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
        os.write(fd, device_description())
        fcntl.ioctl(fd, UI_DEV_CREATE)
        try:
            time.sleep(1.5)                                  # the compositor needs a moment to notice the new device
            for kind, code, value in ((EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_SYN, 0, 0)):
                os.write(fd, event(kind, code, value))
            time.sleep(0.4)                                  # pointer-enter reaches the window before the press
            os.write(fd, event(EV_KEY, BTN_LEFT, 1) + event(EV_SYN, 0, 0))
            time.sleep(hold)
            os.write(fd, event(EV_KEY, BTN_LEFT, 0) + event(EV_SYN, 0, 0))
            time.sleep(0.5)
        finally:
            fcntl.ioctl(fd, UI_DEV_DESTROY)                  # nothing is left behind on the board
    finally:
        os.close(fd)


if __name__ == "__main__":
    pointer = parse(sys.argv[1:])
    click(*pointer)
    print("clicked", *pointer)
