import fcntl
import json
from pathlib import Path
import time

import pytest
from bench.physical import PhysicalBench


@pytest.fixture(scope='session')
def physical_bench(pytestconfig):
    path = pytestconfig.getoption('--hardware-config')
    if not path:
        pytest.skip('real hardware not selected; pass --hardware-config explicitly')
    config = json.loads(Path(path).read_text())  # Missing/invalid selected config is an ERROR, never a skip.
    lock = Path.home() / '.cache/capstone-hardware-bench.lock'
    lock.parent.mkdir(exist_ok=True)
    with lock.open('a') as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pytest.fail('this controller already owns an active physical bench session')
        output = pytestconfig.getoption('--hardware-output') or '.demo/hardware-' + time.strftime('%Y%m%d-%H%M%S')
        bench = PhysicalBench(config, output)
        try:
            bench.prepare()
            yield bench
        finally:
            bench.close()
