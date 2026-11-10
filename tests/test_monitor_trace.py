"""candump trace parsing, vehicle/echo frame decoding and counter pairing."""
import pytest

from capstone_ota.monitor import trace
from capstone_ota.qt_source import frame


def source(t, speed, counter):
    return trace.CanFrame(t, 'can0', 0x200, bytes.fromhex(frame(speed, counter, rpm=2000, gear=3)['data']))


def echo(t, speed, counter, valid=True):
    return trace.CanFrame(t, 'can0', 0x300, trace.encode_echo(speed, counter, valid=valid))


def test_parse_line_and_roundtrip():
    parsed = trace.parse_candump_line('(1730000000.123456) can0 200#0FA005DC0300FF21')
    assert parsed == trace.CanFrame(1730000000.123456, 'can0', 0x200, bytes.fromhex('0FA005DC0300FF21'))
    assert trace.parse_candump_line(trace.format_candump_line(parsed)) == parsed
    assert trace.parse_candump_line('') is None and trace.parse_candump_line('# comment') is None


@pytest.mark.parametrize('line', ['garbage', '(1.0) can0 2000#00', '(1.0) can0 200#0', '(x) can0 200#00'])
def test_parse_line_rejects_malformed(line):
    with pytest.raises(ValueError):
        trace.parse_candump_line(line)


def test_parse_text_orders_nothing_and_skips_blank():
    text = '(1.000000) can0 200#0000000000000000\n\n(2.000000) can0 300#00\n'
    assert [f.t for f in trace.parse_candump(text)] == [1.0, 2.0]


def test_decode_vehicle_matches_source_frame():
    vehicle = trace.decode_vehicle(source(0, 620, 243).data)
    assert (vehicle.speed, vehicle.rpm, vehicle.gear, vehicle.counter) == (620, 2000, 3, 243)


def test_decode_vehicle_rejects_bad_crc_and_length():
    raw = bytearray(source(0, 620, 1).data)
    raw[-1] ^= 1
    with pytest.raises(ValueError):
        trace.decode_vehicle(bytes(raw))
    with pytest.raises(ValueError):
        trace.decode_vehicle(b'\x00' * 7)


def test_echo_roundtrip_and_crc():
    data = trace.encode_echo(1234, 77, valid=False, start_id=5)
    decoded = trace.decode_echo(data)
    assert (decoded.speed, decoded.counter, decoded.valid, decoded.start_id) == (1234, 77, False, 5)
    corrupt = bytearray(data)
    corrupt[0] ^= 1
    with pytest.raises(ValueError):
        trace.decode_echo(bytes(corrupt))


def test_pair_latency_and_unmatched():
    frames = []
    for i in range(10):
        frames.append(source(i * 0.1, 500 + i, i))
        if i != 4:                                   # echo for counter 4 never arrives
            frames.append(echo(i * 0.1 + 0.012, 500 + i, i))
    result = trace.pair_frames(frames)
    assert [p.counter for p in result.pairs] == [0, 1, 2, 3, 5, 6, 7, 8, 9]
    assert all(p.latency == pytest.approx(0.012) for p in result.pairs)
    assert [s.counter for s in result.unmatched_sources] == [4]
    assert result.invalid == 0


def test_pair_uses_latest_source_after_counter_wrap():
    frames = []
    for i in range(300):                             # counter wraps after 255
        frames.append(source(i * 0.1, 400, i % 256))
    frames.append(echo(299 * 0.1 + 0.02, 400, 299 % 256))
    result = trace.pair_frames(frames)
    assert len(result.pairs) == 1
    assert result.pairs[0].t_source == pytest.approx(299 * 0.1)


def test_pair_drops_echo_without_source_and_counts_invalid():
    bad = bytearray(echo(1.0, 100, 1).data)
    bad[-1] ^= 1
    frames = [source(0.0, 100, 1), trace.CanFrame(0.5, 'can0', 0x300, bytes(bad)),
              echo(0.6, 100, 9),                      # no source with counter 9
              trace.CanFrame(0.7, 'can0', 0x123, b'\x00' * 8)]
    result = trace.pair_frames(frames)
    assert result.pairs == [] and result.invalid == 1
    assert [e.counter for e in result.unmatched_echoes] == [9]


def test_pair_ignores_echo_older_than_max_age():
    frames = [source(0.0, 100, 1), echo(5.0, 100, 1)]
    assert trace.pair_frames(frames, max_age=2.0).pairs == []
