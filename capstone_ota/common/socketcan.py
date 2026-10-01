"""Deferred Linux SocketCAN transport for the eight-byte Classic CAN contract.

Import and construction are portable; open() requires AF_CAN support. No CAN
FD mode is enabled. Linux's 16-byte struct can_frame stores the identifier in
native byte order, distinct from the big-endian application payload. Invalid
wire envelopes raise ValueError; codecs separately check payload semantics.
Own one receive loop per transport: recv() sets the socket timeout per call.
"""
from __future__ import annotations

import math
import socket
import struct
from typing import Protocol, runtime_checkable

from .can_protocol import CanFrame


_KERNEL_FRAME = struct.Struct("=IB3x8s")


@runtime_checkable
class CanTransport(Protocol):
    def send(self, frame: CanFrame) -> None: ...

    def recv(self, timeout: float) -> CanFrame | None: ...


class SocketCanTransport:
    def __init__(self, interface: str):
        if type(interface) is not str or not interface or "\0" in interface:
            raise ValueError("CAN interface must be a nonempty name without NUL")
        self.interface = interface
        self._socket: socket.socket | None = None

    def open(self) -> None:
        """Open and bind once. Failed binds close their socket and permit retry."""
        if self._socket is not None:
            return
        if not all(hasattr(socket, name) for name in ("AF_CAN", "SOCK_RAW", "CAN_RAW")):
            raise RuntimeError("SocketCAN is unavailable on this platform")
        raw = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            raw.bind((self.interface,))
        except BaseException:
            raw.close()
            raise
        self._socket = raw

    def close(self) -> None:
        raw, self._socket = self._socket, None
        if raw is not None:
            raw.close()

    def __enter__(self) -> SocketCanTransport:
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _opened_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("SocketCAN transport must be open")
        return self._socket

    def send(self, frame: CanFrame) -> None:
        raw = self._opened_socket()
        if not isinstance(frame, CanFrame):
            raise ValueError("expected CanFrame")
        data = _KERNEL_FRAME.pack(frame.can_id, 8, frame.data)
        if raw.send(data) != _KERNEL_FRAME.size:
            raise OSError("short SocketCAN frame send")

    def recv(self, timeout: float) -> CanFrame | None:
        raw = self._opened_socket()
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("receive timeout must be a finite nonnegative number")
        raw.settimeout(timeout)
        try:
            data = raw.recv(_KERNEL_FRAME.size)
        except (TimeoutError, BlockingIOError):
            return None
        if len(data) != _KERNEL_FRAME.size:
            raise ValueError("invalid SocketCAN frame size")
        can_id, dlc, payload = _KERNEL_FRAME.unpack(data)
        # Reject EFF, RTR and error flag bits as well as non-11-bit IDs.
        if can_id > 0x7FF or dlc != 8:
            raise ValueError("expected an 11-bit Classic CAN data frame with DLC 8")
        return CanFrame(can_id, payload)
