"""Pure, immutable Classic CAN v1 codecs; all multibyte signals are big-endian.

Every ID is 11-bit and every payload is eight bytes. Byte 7 is CRC-8/SMBUS:
polynomial 0x07, init 0, no reflection, xorout 0, computed over bytes 0..6
(not the arbitration ID). Check value for b'123456789' is 0xF4.

0x600/601: enum[0], uint32 transaction token[1:5], counter:4 | slot:4[5],
flags/detail[6]. Slot A=0, B=1; counters are modulo 16.
0x100/101: software major/minor/patch[0:3], protocol major/minor[3:5],
application state[5], modulo-256 counter[6]. ECU role selects heartbeat ID.
0x200/610/611: uint16 speed (0.1 km/h)[0:2], uint16 RPM[2:4], gear[4],
warning bitmask[5], modulo-256 counter (vehicle) / test ID (functional)[6].
Functional results carry interpreted application values, compared by caller.

CRC and counters are error/freshness checks, not authentication. Token history,
collision handling, elapsed-time freshness and physical signal limits belong
to the transaction/health layers. No socket or platform I/O occurs here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum


class OtaCommand(IntEnum):
    PREPARE = 1
    ACTIVATE = 2
    COMMIT = 3
    ROLLBACK = 4
    QUERY_STATUS = 5


class OtaStatus(IntEnum):
    IDLE = 0
    PREPARING = 1
    READY = 2
    ACTIVATING = 3
    VERIFYING = 4
    COMMITTED = 5
    ROLLING_BACK = 6
    ROLLED_BACK = 7
    ERROR = 8
    ABORTED = 9
    RECOVERY_FAILED = 10


class Slot(IntEnum):
    A = 0
    B = 1


class ApplicationState(IntEnum):
    STABLE = 0
    TRIAL = 1


class Gear(IntEnum):
    PARK = 0
    REVERSE = 1
    NEUTRAL = 2
    DRIVE = 3


def _uint(value: int, bits: int, name: str) -> int:
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"{name} must be a uint{bits}")
    return value


def _enum(value: int, enum: type[IntEnum]) -> IntEnum:
    if type(value) not in (int, enum):
        raise ValueError(f"invalid {enum.__name__} enum")
    try:
        return enum(value)
    except ValueError as exc:
        raise ValueError(f"unknown {enum.__name__} enum") from exc


@dataclass(frozen=True)
class CanFrame:
    can_id: int
    data: bytes

    def __post_init__(self) -> None:
        _uint(self.can_id, 11, "CAN identifier")
        if type(self.data) is not bytes or len(self.data) != 8:
            raise ValueError("Classic CAN v1 payload must be exactly eight immutable bytes")


def crc8(data: bytes) -> int:
    """CRC-8/SMBUS, width 8, poly 0x07, init/xorout 0, refin/refout false."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return crc


def _frame(can_id: int, payload: bytes) -> CanFrame:
    return CanFrame(can_id, payload + bytes([crc8(payload)]))


def _payload(frame: CanFrame, ids: tuple[int, ...]) -> bytes:
    if not isinstance(frame, CanFrame):
        raise ValueError("expected CanFrame")
    if frame.can_id not in ids:
        raise ValueError("wrong CAN identifier for codec")
    if crc8(frame.data[:7]) != frame.data[7]:
        raise ValueError("invalid CRC-8")
    return frame.data[:7]


def _transaction_payload(kind: IntEnum, token: int, slot: Slot,
                         detail: int, counter: int) -> bytes:
    token = _uint(token, 32, "transaction_token")
    slot = _enum(slot, Slot)
    detail = _uint(detail, 8, "flags/detail")
    counter = _uint(counter, 4, "counter")
    return struct.pack(">BIBB", kind, token, counter << 4 | slot, detail)


def _transaction_fields(frame: CanFrame, can_id: int, enum: type[IntEnum]) -> tuple:
    kind, token, packed, detail = struct.unpack(">BIBB", _payload(frame, (can_id,)))
    return _enum(kind, enum), token, _enum(packed & 15, Slot), detail, packed >> 4


@dataclass(frozen=True)
class OtaCommandFrame:
    command: OtaCommand
    transaction_token: int
    slot: Slot
    flags: int = 0
    counter: int = 0

    def encode(self) -> CanFrame:
        return _frame(0x600, _transaction_payload(_enum(self.command, OtaCommand),
                      self.transaction_token, self.slot, self.flags, self.counter))

    @classmethod
    def decode(cls, frame: CanFrame) -> OtaCommandFrame:
        return cls(*_transaction_fields(frame, 0x600, OtaCommand))


@dataclass(frozen=True)
class OtaStatusFrame:
    status: OtaStatus
    transaction_token: int
    slot: Slot
    detail: int = 0
    counter: int = 0

    def encode(self) -> CanFrame:
        return _frame(0x601, _transaction_payload(_enum(self.status, OtaStatus),
                      self.transaction_token, self.slot, self.detail, self.counter))

    @classmethod
    def decode(cls, frame: CanFrame) -> OtaStatusFrame:
        return cls(*_transaction_fields(frame, 0x601, OtaStatus))


_HEARTBEAT_IDS = {"central-control": 0x100, "digital-cluster": 0x101}


@dataclass(frozen=True)
class HeartbeatFrame:
    ecu_id: str
    software_version: tuple[int, int, int]
    protocol_major: int
    protocol_minor: int
    state: ApplicationState
    counter: int

    def encode(self) -> CanFrame:
        if type(self.ecu_id) is not str or self.ecu_id not in _HEARTBEAT_IDS:
            raise ValueError("unknown heartbeat ECU role")
        if type(self.software_version) is not tuple or len(self.software_version) != 3:
            raise ValueError("software_version must be a numeric triple")
        version = tuple(_uint(v, 8, "software_version") for v in self.software_version)
        return _frame(_HEARTBEAT_IDS[self.ecu_id], struct.pack("7B", *version,
                      _uint(self.protocol_major, 8, "protocol_major"),
                      _uint(self.protocol_minor, 8, "protocol_minor"),
                      _enum(self.state, ApplicationState), _uint(self.counter, 8, "counter")))

    @classmethod
    def decode(cls, frame: CanFrame) -> HeartbeatFrame:
        values = struct.unpack("7B", _payload(frame, tuple(_HEARTBEAT_IDS.values())))
        role = next(role for role, can_id in _HEARTBEAT_IDS.items() if can_id == frame.can_id)
        return cls(role, values[:3], values[3], values[4],
                   _enum(values[5], ApplicationState), values[6])


def _signals(speed: int, rpm: int, gear: Gear, warnings: int, sequence: int) -> bytes:
    return struct.pack(">HHBBB", _uint(speed, 16, "speed"), _uint(rpm, 16, "rpm"),
                       _enum(gear, Gear), _uint(warnings, 8, "warnings"),
                       _uint(sequence, 8, "counter/test_id"))


def _signal_fields(frame: CanFrame, can_id: int) -> tuple:
    speed, rpm, gear, warnings, sequence = struct.unpack(">HHBBB", _payload(frame, (can_id,)))
    return speed, rpm, _enum(gear, Gear), warnings, sequence


@dataclass(frozen=True)
class VehicleStatusFrame:
    speed: int
    rpm: int
    gear: Gear
    warnings: int
    counter: int

    def encode(self) -> CanFrame:
        return _frame(0x200, _signals(self.speed, self.rpm, self.gear, self.warnings, self.counter))

    @classmethod
    def decode(cls, frame: CanFrame) -> VehicleStatusFrame:
        return cls(*_signal_fields(frame, 0x200))


@dataclass(frozen=True)
class FunctionalTestRequestFrame:
    speed: int
    rpm: int
    gear: Gear
    warnings: int
    test_id: int

    def encode(self) -> CanFrame:
        return _frame(0x610, _signals(self.speed, self.rpm, self.gear, self.warnings, self.test_id))

    @classmethod
    def decode(cls, frame: CanFrame) -> FunctionalTestRequestFrame:
        return cls(*_signal_fields(frame, 0x610))


@dataclass(frozen=True)
class FunctionalTestResultFrame:
    speed: int
    rpm: int
    gear: Gear
    warnings: int
    test_id: int

    def encode(self) -> CanFrame:
        return _frame(0x611, _signals(self.speed, self.rpm, self.gear, self.warnings, self.test_id))

    @classmethod
    def decode(cls, frame: CanFrame) -> FunctionalTestResultFrame:
        return cls(*_signal_fields(frame, 0x611))


def is_counter_fresh(counter: int, previous: int | None, *, bits: int = 8) -> bool:
    """Accept initial sample or forward modulo distance strictly below half-range.

    Caller stores only accepted counters and enforces elapsed-time deadlines.
    Use bits=4 for command/status; bits=8 for heartbeat/vehicle. An exact half
    jump is ambiguous and rejected. This does not authorize counter resets.
    """
    if type(bits) is not int or not 1 <= bits <= 8:
        raise ValueError("counter width must be between 1 and 8")
    _uint(counter, bits, "counter")
    if previous is None:
        return True
    _uint(previous, bits, "previous")
    distance = (counter - previous) % (1 << bits)
    return 0 < distance < 1 << (bits - 1)


def matches_transaction_token(token: int, expected_token: int) -> bool:
    """Compare uint32 tokens; authorization and retained history are caller-owned."""
    return _uint(token, 32, "token") == _uint(expected_token, 32, "expected_token")
