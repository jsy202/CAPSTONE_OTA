"""Literal v1 wire vectors catch byte/bit placement, byte order, and CRC errors."""
from dataclasses import replace

import pytest

from capstone_ota.common import can_protocol


@pytest.fixture
def protocol():
    return can_protocol


def samples(p):
    return [
        (p.OtaCommandFrame(p.OtaCommand.PREPARE, 0x550E8400, p.Slot.B, 0xA5, 3),
         0x600, "01550e840031a51f"),
        (p.OtaStatusFrame(p.OtaStatus.READY, 0x550E8400, p.Slot.B, 7, 3),
         0x601, "02550e840031071e"),
        (p.HeartbeatFrame("central-control", (1, 2, 3), 1, 2, p.ApplicationState.TRIAL, 254),
         0x100, "010203010201fe0a"),
        (p.HeartbeatFrame("digital-cluster", (1, 2, 3), 1, 2, p.ApplicationState.TRIAL, 254),
         0x101, "010203010201fe0a"),
        (p.VehicleStatusFrame(1234, 5678, p.Gear.DRIVE, 10, 255),
         0x200, "04d2162e030aff9c"),
        (p.FunctionalTestRequestFrame(1234, 5678, p.Gear.DRIVE, 10, 42),
         0x610, "04d2162e030a2ab9"),
        (p.FunctionalTestResultFrame(1234, 5678, p.Gear.DRIVE, 9, 42),
         0x611, "04d2162e03092a86"),
    ]


def test_all_exact_wire_vectors_and_round_trips(protocol):
    for message, can_id, payload in samples(protocol):
        frame = message.encode()
        assert frame.can_id == can_id
        assert frame.data == bytes.fromhex(payload)
        assert type(message).decode(protocol.CanFrame(can_id, bytes.fromhex(payload))) == message


def test_crc_smbus_known_check_and_zero_vectors(protocol):
    assert protocol.crc8(b"123456789") == 0xF4
    assert protocol.crc8(b"\x00" * 7) == 0
    assert protocol.crc8(b"") == 0


@pytest.mark.parametrize("name,number,payload", [
    ("PREPARE", 1, "01550e840031a51f"),
    ("ACTIVATE", 2, "02550e840031a579"),
    ("COMMIT", 3, "03550e840031a5a6"),
    ("ROLLBACK", 4, "04550e840031a5b5"),
    ("QUERY_STATUS", 5, "05550e840031a56a"),
])
def test_all_command_enum_numeric_values_have_exact_wire_vectors(protocol, name, number, payload):
    command = protocol.OtaCommand[name]
    assert int(command) == number
    message = protocol.OtaCommandFrame(command, 0x550E8400, protocol.Slot.B, 0xA5, 3)
    frame = protocol.CanFrame(0x600, bytes.fromhex(payload))
    assert message.encode() == frame
    assert protocol.OtaCommandFrame.decode(frame) == message


@pytest.mark.parametrize("name,number,payload", [
    ("IDLE", 0, "00550e84003107a7"),
    ("PREPARING", 1, "01550e8400310778"),
    ("READY", 2, "02550e840031071e"),
    ("ACTIVATING", 3, "03550e84003107c1"),
    ("VERIFYING", 4, "04550e84003107d2"),
    ("COMMITTED", 5, "05550e840031070d"),
    ("ROLLING_BACK", 6, "06550e840031076b"),
    ("ROLLED_BACK", 7, "07550e84003107b4"),
    ("ERROR", 8, "08550e840031074d"),
    ("ABORTED", 9, "09550e8400310792"),
    ("RECOVERY_FAILED", 10, "0a550e84003107f4"),
])
def test_all_status_enum_numeric_values_have_exact_wire_vectors(protocol, name, number, payload):
    status = protocol.OtaStatus[name]
    assert int(status) == number
    message = protocol.OtaStatusFrame(status, 0x550E8400, protocol.Slot.B, 7, 3)
    frame = protocol.CanFrame(0x601, bytes.fromhex(payload))
    assert message.encode() == frame
    assert protocol.OtaStatusFrame.decode(frame) == message


@pytest.mark.parametrize("bad", [-1, 0x800, True, 0x80000100])
def test_raw_frame_rejects_non_standard_ids(protocol, bad):
    with pytest.raises(ValueError):
        protocol.CanFrame(bad, b"\0" * 8)


@pytest.mark.parametrize("bad", [b"", b"\0" * 7, b"\0" * 9, bytearray(8), "12345678"])
def test_raw_frame_requires_exact_immutable_eight_bytes(protocol, bad):
    with pytest.raises(ValueError):
        protocol.CanFrame(0x100, bad)


def test_each_codec_rejects_wrong_id_and_corrupt_crc(protocol):
    for message, can_id, payload in samples(protocol):
        with pytest.raises(ValueError):
            type(message).decode(protocol.CanFrame(0x7FF, bytes.fromhex(payload)))
        corrupt = bytes.fromhex(payload)[:-1] + bytes([int(payload[-2:], 16) ^ 1])
        with pytest.raises(ValueError, match="CRC"):
            type(message).decode(protocol.CanFrame(can_id, corrupt))


@pytest.mark.parametrize("index,payload", [
    (0, "ff550e840031a584"), (1, "ff550e840031a584"),
    (2, "0102030102fffec8"), (4, "04d2162eff0aff0a"),
    (5, "04d2162eff0a2a2f"), (6, "04d2162eff092a10"),
    (0, "01550e840032a520"),
])
def test_unknown_enums_and_reserved_slot_nibbles_rejected(protocol, index, payload):
    message, can_id, _ = samples(protocol)[index]
    with pytest.raises(ValueError, match="enum|slot"):
        type(message).decode(protocol.CanFrame(can_id, bytes.fromhex(payload)))


@pytest.mark.parametrize("index,field,bad", [
    (0, "transaction_token", -1), (0, "transaction_token", 2**32),
    (0, "transaction_token", True), (0, "flags", 256), (0, "counter", 16),
    (0, "command", 255), (0, "slot", 2),
    (1, "status", 255), (1, "detail", -1), (1, "counter", 16),
    (2, "ecu_id", "other"), (2, "software_version", (1, 2)),
    (2, "software_version", (256, 0, 0)), (2, "software_version", (True, 0, 0)),
    (2, "protocol_major", 256), (2, "protocol_minor", -1), (2, "counter", 256),
    (2, "state", 255), (4, "speed", 65536), (4, "rpm", -1),
    (4, "gear", 4), (4, "warnings", True), (4, "counter", -1),
    (5, "test_id", 256), (6, "rpm", 65536), (6, "warnings", 256),
])
def test_out_of_range_and_non_integer_fields_rejected(protocol, index, field, bad):
    message, _, _ = samples(protocol)[index]
    with pytest.raises(ValueError):
        replace(message, **{field: bad}).encode()


def test_numeric_wire_boundaries_are_lossless(protocol):
    for message in [
        protocol.OtaCommandFrame(protocol.OtaCommand.QUERY_STATUS, 0xFFFFFFFF,
                                 protocol.Slot.A, 255, 15),
        protocol.VehicleStatusFrame(65535, 65535, protocol.Gear.PARK, 255, 0),
        protocol.HeartbeatFrame("digital-cluster", (255, 255, 255), 255, 255,
                                protocol.ApplicationState.STABLE, 0),
    ]:
        assert type(message).decode(message.encode()) == message


@pytest.mark.parametrize("counter,previous,bits,expected", [
    (0, None, 8, True), (11, 10, 8, True), (10, 10, 8, False),
    (9, 10, 8, False), (0, 255, 8, True), (255, 0, 8, False),
    (12, 10, 8, False), (127, 0, 8, False), (128, 0, 8, False), (0, 15, 4, True),
    (7, 0, 4, False), (8, 0, 4, False), (1, 255, 8, False), (1, 15, 4, False),
])
def test_primary_counter_validation_rejects_gaps_and_accepts_single_step_rollover(
        protocol, counter, previous, bits, expected):
    assert protocol.is_counter_fresh(counter, previous, bits=bits) is expected


@pytest.mark.parametrize("counter,previous,bits,expected", [
    (10, None, 8, True), (11, 10, 8, True), (12, 10, 8, False),
    (137, 10, 8, False), (10, 10, 8, False), (9, 10, 8, False),
    (0, 255, 8, True), (1, 255, 8, False), (0, 15, 4, True),
    (1, 15, 4, False),
])
def test_explicit_contiguous_counter_helper_detects_loss(protocol, counter, previous, bits, expected):
    assert hasattr(protocol, "is_counter_contiguous"), "loss-detecting helper is missing"
    assert protocol.is_counter_contiguous(counter, previous, bits=bits) is expected


@pytest.mark.parametrize("counter,previous,bits", [(256, 0, 8), (0, -1, 8),
    (0, None, 0), (0, 0, True), (True, None, 8)])
def test_counter_helper_rejects_invalid_inputs(protocol, counter, previous, bits):
    with pytest.raises(ValueError):
        protocol.is_counter_fresh(counter, previous, bits=bits)


@pytest.mark.parametrize("counter,previous,bits", [(256, 0, 8), (0, -1, 8),
    (0, None, 0), (0, 0, True), (True, None, 8)])
def test_contiguous_counter_helper_rejects_invalid_inputs(protocol, counter, previous, bits):
    assert hasattr(protocol, "is_counter_contiguous"), "loss-detecting helper is missing"
    with pytest.raises(ValueError):
        protocol.is_counter_contiguous(counter, previous, bits=bits)


def test_transaction_token_helper_requires_exact_uint32_match(protocol):
    assert protocol.matches_transaction_token(0x550E8400, 0x550E8400)
    assert not protocol.matches_transaction_token(0x550E8400, 0x550E8401)
    for token, expected in [(-1, 0), (0, 2**32), (True, 1)]:
        with pytest.raises(ValueError):
            protocol.matches_transaction_token(token, expected)
