from types import SimpleNamespace
import pytest
from bench.physical import PhysicalBench


def test_cleanup_continues_after_link_collection_fails():
    events = []
    bench = object.__new__(PhysicalBench)
    def broken_links():
        raise OSError('link collection unavailable')
    bench.links = broken_links
    bench.record = lambda *args: None
    bench.captures = SimpleNamespace(close=lambda: events.append('captures') or [])
    bench.last_good = ('device', 'observer')
    def select():
        events.append('restore')
        raise OSError('restore unavailable')
    bench.select_good = select
    bench.server = None
    bench.secrets = SimpleNamespace(cleanup=lambda: events.append('secrets'))
    with pytest.raises(RuntimeError, match='link collection unavailable'):
        bench.close()
    assert events == ['captures', 'restore', 'secrets']
