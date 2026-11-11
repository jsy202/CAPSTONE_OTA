"""Authenticated pass/fail verdict transport over the physical CAN bus.

The external verifier's pass/fail judgment is the one input Device trusts to
drive a rollback. SSH may still *trigger* a check (its failure is already
fail-safe: no check happens), but the verdict itself now travels only on the
CAN bus the project's threat model already assumes is physically present,
authenticated with a pre-shared key so a bus-tap attacker cannot forge a
PASS (or force a FAIL as a denial-of-service) without it.
"""
import hashlib
import hmac
import secrets
import socket
import struct
import time

VERDICT_CAN_ID = 0x7F1
_FRAME = struct.Struct('=IB3x8s')  # Linux struct can_frame: id, len, pad[3], data[8]
_PAYLOAD = struct.Struct('>8sB8s')  # nonce(8) + passed(1) + mac(8)
_CHUNK = 7  # data bytes per CAN frame after the 1-byte sequence header


def new_nonce() -> bytes:
    return secrets.token_bytes(8)


def _mac(secret: bytes, nonce: bytes, passed: bool) -> bytes:
    return hmac.new(secret, nonce + bytes([1 if passed else 0]), hashlib.sha256).digest()[:8]


def _chunks(payload: bytes):
    total = -(-len(payload) // _CHUNK)
    for index in range(total):
        piece = payload[index * _CHUNK:(index + 1) * _CHUNK]
        yield bytes([(index << 4) | total]) + piece.ljust(_CHUNK, b'\x00')


def send_verdict(channel: str, secret: bytes, nonce: bytes, passed: bool) -> None:
    if len(nonce) != 8:
        raise ValueError('nonce must be 8 bytes')
    payload = _PAYLOAD.pack(nonce, 1 if passed else 0, _mac(secret, nonce, passed))
    with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as bus:
        bus.bind((channel,))
        for data in _chunks(payload):
            bus.send(_FRAME.pack(VERDICT_CAN_ID, 8, data))


def open_listener(channel: str) -> socket.socket:
    """Bind and filter a verdict-listening socket. Open this *before* triggering the
    remote check (e.g. over SSH): a CAN frame sent while nobody is bound yet to hear
    it is simply gone, so binding late loses a genuine verdict to a timing race."""
    bus = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    bus.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack('=II', VERDICT_CAN_ID, 0x1FFFFFFF))
    bus.bind((channel,))
    return bus


def await_verdict(bus: socket.socket, secret: bytes, nonce: bytes, timeout: float = 5.0) -> bool:
    """Wait on an already-open listener for our nonce's authenticated verdict.
    Anything else (timeout, wrong nonce, bad MAC) is ignored and never trusted;
    only a genuine, freshly-matching, correctly-authenticated frame set returns one."""
    if len(nonce) != 8:
        raise ValueError('nonce must be 8 bytes')
    deadline = time.monotonic() + timeout
    pieces, total = {}, None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        bus.settimeout(remaining)
        try:
            raw = bus.recv(16)
        except (socket.timeout, OSError):
            break
        can_id, length, data = _FRAME.unpack(raw)
        if can_id != VERDICT_CAN_ID or length != 8:
            continue
        header, piece = data[0], data[1:]
        index, declared_total = header >> 4, header & 0xF
        if total != declared_total:
            pieces, total = {}, declared_total
        pieces[index] = piece
        if not (total and len(pieces) >= total):
            continue
        payload = b''.join(pieces[i] for i in range(total))[:_PAYLOAD.size]
        got_nonce, passed_byte, mac = _PAYLOAD.unpack(payload)
        passed = bool(passed_byte)
        if got_nonce == nonce and hmac.compare_digest(mac, _mac(secret, nonce, passed)):
            return passed
        pieces, total = {}, None  # stale/foreign/forged round trip; keep listening
    raise TimeoutError('no authenticated verdict received on the CAN bus')


def receive_verdict(channel: str, secret: bytes, nonce: bytes, timeout: float = 5.0) -> bool:
    """Convenience form for callers who can bind and wait in one step (no trigger
    race to avoid, e.g. tests). See open_listener()'s note for why real callers that
    trigger a remote check should bind first with open_listener() instead."""
    with open_listener(channel) as bus:
        return await_verdict(bus, secret, nonce, timeout)
