"""Socket boundary doubles avoid CAN hardware while checking real adapter output."""
import socket
import struct
from types import SimpleNamespace

import pytest

from capstone_ota.common.can_protocol import CanFrame
from capstone_ota.common import socketcan


@pytest.fixture
def adapter():
    return socketcan


class RawSocket:
    def __init__(self):
        self.bound = None
        self.closed = False
        self.outgoing = []
        self.incoming = []
        self.timeout = None
        self.receive_timeouts = []
        self.require_blocking_send = False
        self.bind_error = None
        self.short_send = False

    def bind(self, address):
        if self.bind_error:
            raise self.bind_error
        self.bound = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def send(self, data):
        if self.require_blocking_send and self.timeout == 0:
            raise BlockingIOError("CAN transmit queue is busy")
        self.outgoing.append(data)
        return len(data) - 1 if self.short_send else len(data)

    def recv(self, size):
        assert size == 16
        self.receive_timeouts.append(self.timeout)
        if not self.incoming:
            raise socket.timeout()
        result = self.incoming.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


def install_socket(adapter, monkeypatch, raw):
    creations = []

    def factory(*args):
        assert args == (29, 3, 1)  # AF_CAN, SOCK_RAW, CAN_RAW boundary
        creations.append(raw)
        return raw

    monkeypatch.setattr(adapter, "socket", SimpleNamespace(
        AF_CAN=29, SOCK_RAW=3, CAN_RAW=1, socket=factory))
    return creations


def test_open_is_deferred_idempotent_and_binds_interface(adapter, monkeypatch):
    raw = RawSocket()
    creations = install_socket(adapter, monkeypatch, raw)
    transport = adapter.SocketCanTransport("can0")
    assert creations == []
    transport.open()
    transport.open()
    assert creations == [raw]
    assert raw.bound == ("can0",)
    transport.close()
    transport.close()
    assert raw.closed


def test_kernel_abi_output_and_receive_decode(adapter, monkeypatch):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    frame = CanFrame(0x600, bytes.fromhex("01550e840031a51f"))
    kernel_frame = struct.pack("=I", 0x600) + b"\x08\x00\x00\x00" + frame.data
    raw.incoming.append(kernel_frame)
    with adapter.SocketCanTransport("vcan0") as transport:
        assert isinstance(transport, adapter.CanTransport)
        transport.send(frame)
        assert raw.outgoing == [kernel_frame]
        assert transport.recv(0.25) == frame
        assert raw.receive_timeouts == [0.25]
        assert raw.timeout is None
    assert raw.closed


@pytest.mark.parametrize("failure", [socket.timeout(), BlockingIOError()])
def test_receive_timeout_and_nonblocking_empty_return_none(adapter, monkeypatch, failure):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    raw.incoming.append(failure)
    with adapter.SocketCanTransport("can0") as transport:
        assert transport.recv(0) is None


@pytest.mark.parametrize("can_id,dlc", [(0x80000100, 8), (0x40000100, 8),
    (0x20000100, 8), (0x800, 8), (0x100, 7), (0x100, 9)])
def test_non_classic_non_data_or_wrong_dlc_rejected(adapter, monkeypatch, can_id, dlc):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    raw.incoming.append(struct.pack("=I", can_id) + bytes([dlc, 0, 0, 0]) + b"\0" * 8)
    with adapter.SocketCanTransport("can0") as transport:
        with pytest.raises(ValueError):
            transport.recv(1)


@pytest.mark.parametrize("data", [b"", b"\0" * 15, b"\0" * 17])
def test_truncated_or_oversized_kernel_frame_rejected(adapter, monkeypatch, data):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    raw.incoming.append(data)
    with adapter.SocketCanTransport("can0") as transport:
        with pytest.raises(ValueError):
            transport.recv(1)


def test_failed_bind_closes_socket_and_allows_retry(adapter, monkeypatch):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    raw.bind_error = OSError("missing CAN interface")
    transport = adapter.SocketCanTransport("can0")
    with pytest.raises(OSError, match="missing CAN interface"):
        transport.open()
    assert raw.closed
    with pytest.raises(RuntimeError, match="open"):
        transport.recv(0)
    raw.bind_error = None
    transport.open()
    assert raw.bound == ("can0",)
    transport.close()


def test_unsupported_platform_fails_only_when_opening(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "socket", SimpleNamespace())
    transport = adapter.SocketCanTransport("can0")
    transport.close()
    with pytest.raises(RuntimeError, match="SocketCAN"):
        transport.open()


def test_closed_transport_requires_explicit_open(adapter):
    transport = adapter.SocketCanTransport("can0")
    with pytest.raises(RuntimeError, match="open"):
        transport.send(CanFrame(0x100, b"\0" * 8))
    with pytest.raises(RuntimeError, match="open"):
        transport.recv(0)


@pytest.mark.parametrize("interface", ["", None, 1, "can\0"])
def test_invalid_interface_rejected_before_io(adapter, interface):
    with pytest.raises(ValueError):
        adapter.SocketCanTransport(interface)


@pytest.mark.parametrize("timeout", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_receive_timeout_rejected(adapter, monkeypatch, timeout):
    raw = RawSocket()
    install_socket(adapter, monkeypatch, raw)
    with adapter.SocketCanTransport("can0") as transport:
        with pytest.raises(ValueError):
            transport.recv(timeout)


def test_short_send_and_real_io_errors_propagate(adapter, monkeypatch):
    raw = RawSocket()
    raw.short_send = True
    install_socket(adapter, monkeypatch, raw)
    with adapter.SocketCanTransport("can0") as transport:
        with pytest.raises(OSError, match="short"):
            transport.send(CanFrame(0x100, b"\0" * 8))
        raw.incoming.append(OSError("bus failure"))
        with pytest.raises(OSError, match="bus failure"):
            transport.recv(1)


@pytest.mark.parametrize("previous_timeout", [None, 2.5])
def test_nonblocking_receive_restores_send_mode(adapter, monkeypatch, previous_timeout):
    raw = RawSocket()
    raw.timeout = previous_timeout
    raw.require_blocking_send = True
    install_socket(adapter, monkeypatch, raw)
    frame = CanFrame(0x100, b"\0" * 8)
    with adapter.SocketCanTransport("can0") as transport:
        assert transport.recv(0) is None
        transport.send(frame)  # Busy socket would fail if recv left nonblocking mode enabled.
        assert raw.outgoing == [struct.pack("=I", 0x100) + b"\x08\0\0\0" + frame.data]
        assert raw.timeout == previous_timeout
        assert raw.receive_timeouts == [0]


@pytest.mark.parametrize("incoming,exception", [
    (socket.timeout(), None), (BlockingIOError(), None),
    (OSError("bus failure"), OSError), (b"invalid frame", ValueError),
])
def test_receive_restores_previous_timeout_on_every_exit(adapter, monkeypatch, incoming, exception):
    raw = RawSocket()
    raw.timeout = 1.5
    raw.incoming.append(incoming)
    install_socket(adapter, monkeypatch, raw)
    with adapter.SocketCanTransport("can0") as transport:
        if exception:
            with pytest.raises(exception):
                transport.recv(0.1)
        else:
            assert transport.recv(0.1) is None
        assert raw.timeout == 1.5
        assert raw.receive_timeouts == [0.1]
