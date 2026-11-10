"""candump trace parsing, vehicle/echo frame decoding and source/echo pairing.

Echo frame (CAN 0x300, sent by the Cluster): >H displayed speed (tenths km/h), B source counter it
reflects, B flags (bit0 = input valid), B service start id, 2 reserved zero bytes, CRC8 over the
first seven bytes plus the data id bytes 00 31. The Cluster side must implement the same layout.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import NamedTuple

VEHICLE_ID = 0x200
ECHO_ID = 0x300
VEHICLE_DATA_ID = b'\x00\x21'
ECHO_DATA_ID = b'\x00\x31'
_LINE = re.compile(r'^\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]{3}|[0-9A-Fa-f]{8})#((?:[0-9A-Fa-f]{2})*)$')


class CanFrame(NamedTuple):
    t: float
    iface: str
    id: int
    data: bytes


class Vehicle(NamedTuple):
    speed: int
    rpm: int
    gear: int
    warnings: int
    counter: int


class Echo(NamedTuple):
    speed: int
    counter: int
    valid: bool
    start_id: int


class Observation(NamedTuple):
    t: float
    counter: int
    speed: int


class Pair(NamedTuple):
    counter: int
    t_source: float
    t_echo: float
    latency: float
    source_speed: int
    echo_speed: int
    echo_valid: bool


class PairResult(NamedTuple):
    pairs: list
    unmatched_sources: list
    unmatched_echoes: list
    invalid: int


def crc8(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ 7 if value & 128 else value << 1) & 255
    return value


def parse_candump_line(line: str) -> CanFrame | None:
    """Parse one `candump -L` line; blank/comment lines give None, anything else malformed raises."""
    text = line.strip()
    if not text or text.startswith('#'):
        return None
    match = _LINE.match(text)
    if not match:
        raise ValueError('malformed candump line')
    stamp, iface, identifier, payload = match.groups()
    return CanFrame(float(stamp), iface, int(identifier, 16), bytes.fromhex(payload))


def parse_candump(source) -> list[CanFrame]:
    text = Path(source).read_text() if isinstance(source, Path) else source
    return [f for f in map(parse_candump_line, text.splitlines()) if f is not None]


def format_candump_line(frame: CanFrame) -> str:
    return f'({frame.t:.6f}) {frame.iface} {frame.id:03X}#{frame.data.hex().upper()}'


def decode_vehicle(data: bytes) -> Vehicle:
    if len(data) != 8 or crc8(data[:7] + VEHICLE_DATA_ID) != data[7]:
        raise ValueError('invalid vehicle frame length or CRC')
    speed, rpm, gear, warnings, counter = struct.unpack('>HHBBB', data[:7])
    return Vehicle(speed, rpm, gear, warnings, counter)


def encode_echo(speed: int, counter: int, valid: bool = True, start_id: int = 0) -> bytes:
    raw = struct.pack('>HBBB2x', speed, counter, 1 if valid else 0, start_id)
    return raw + bytes([crc8(raw + ECHO_DATA_ID)])


def decode_echo(data: bytes) -> Echo:
    if len(data) != 8 or crc8(data[:7] + ECHO_DATA_ID) != data[7]:
        raise ValueError('invalid echo frame length or CRC')
    speed, counter, flags, start_id = struct.unpack('>HBBB2x', data[:7])
    return Echo(speed, counter, bool(flags & 1), start_id)


def pair_frames(frames, max_age: float = 2.0) -> PairResult:
    """Match each echo with the latest earlier source frame carrying the same counter."""
    sources: list[Observation] = []
    latest: dict[int, int] = {}
    matched: set[int] = set()
    pairs, unmatched_echoes, invalid = [], [], 0
    for item in frames:
        try:
            if item.id == VEHICLE_ID:
                vehicle = decode_vehicle(item.data)
                latest[vehicle.counter] = len(sources)
                sources.append(Observation(item.t, vehicle.counter, vehicle.speed))
            elif item.id == ECHO_ID:
                echo = decode_echo(item.data)
                index = latest.get(echo.counter)
                origin = sources[index] if index is not None else None
                if origin is None or not 0 <= item.t - origin.t <= max_age:
                    unmatched_echoes.append(Observation(item.t, echo.counter, echo.speed))
                    continue
                matched.add(index)
                pairs.append(Pair(echo.counter, origin.t, item.t, item.t - origin.t,
                                  origin.speed, echo.speed, echo.valid))
        except ValueError:
            invalid += 1
    unmatched_sources = [s for i, s in enumerate(sources) if i not in matched]
    return PairResult(pairs, unmatched_sources, unmatched_echoes, invalid)
