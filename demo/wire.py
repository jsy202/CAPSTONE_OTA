"""Virtual 0x200 frame payload; CRC detects errors, NOT sender authentication."""
import struct


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 0x07 if value & 0x80 else value << 1) & 255
    return value


def encode(speed, counter):
    if type(speed) is not int or not 0 <= speed <= 3000 or type(counter) is not int or not 0 <= counter <= 255:
        raise ValueError("invalid speed/counter")
    raw = struct.pack(">HHBBB", speed, 1500, 3, 0, counter)
    return {"id": 0x200, "data": (raw + bytes([crc8(raw)])).hex()}


def decode(frame):
    if (not isinstance(frame, dict) or set(frame) != {"id", "data"} or type(frame["id"]) is not int
            or frame["id"] != 0x200 or not isinstance(frame["data"], str) or len(frame["data"]) != 16):
        raise ValueError("invalid virtual frame")
    raw = bytes.fromhex(frame["data"])
    if len(raw) != 8 or crc8(raw[:7]) != raw[7]:
        raise ValueError("invalid frame length/CRC")
    speed, rpm, gear, warnings, counter = struct.unpack(">HHBBB", raw[:7])
    if speed > 3000 or (rpm, gear, warnings) != (1500, 3, 0):
        raise ValueError("invalid demo signal values")
    return {"speed": speed, "counter": counter}
