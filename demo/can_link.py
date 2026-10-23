"""Classic SocketCAN framing for the isolated three-Pi bench."""
import socket
import struct
import time

from .wire import decode, encode

DATA_ID = 0x21
START, REPORT, VEHICLE = 0x610, 0x611, 0x200
FRAME = struct.Struct("=IB3x8s")


def protected_frame(speed, counter, data_id=DATA_ID):
    return encode(speed, counter, data_id)


def decode_protected(frame):
    return decode(frame, DATA_ID)


class CanBus:
    def __init__(self, channel):
        self.socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            self.socket.bind((channel,))
        except BaseException:
            self.socket.close()
            raise

    def close(self):
        self.socket.close()

    def send(self, identifier, data):
        if not 0 <= identifier <= 0x7FF or len(data) != 8:
            raise ValueError("bench requires an eight-byte standard CAN frame")
        self.socket.settimeout(1)
        self.socket.send(FRAME.pack(identifier, len(data), data))

    def receive(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("CAN receive timeout")
            self.socket.settimeout(remaining)
            raw = self.socket.recv(FRAME.size)
            if len(raw) != FRAME.size:
                continue
            identifier, size, data = FRAME.unpack(raw)
            # Extended, error and remote-request frames are outside this bench.
            if identifier > 0x7FF or size != 8:
                continue
            return identifier, data
