"""Assemble a mock IVI release payload (bin/app, libexec/ivi.py, ivi.json)."""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIGS = {   # V2: head unit without a map. V3: adds the map and its background tile-prefetch workers.
    'V2': {'version': 'V2', 'map_enabled': False, 'map_workers': 0, 'map_priority': 'normal'},
    'V3': {'version': 'V3', 'map_enabled': True, 'map_workers': 'cores', 'map_priority': 'rr10'},
}


def payload(output, version, map_file=None):
    output = Path(output).absolute()
    (output / 'bin').mkdir(parents=True)
    (output / 'libexec').mkdir()
    shutil.copy2(HERE / 'app', output / 'bin/app')
    shutil.copy2(HERE / 'ivi.py', output / 'libexec/ivi.py')
    shutil.copy2(HERE / 'nav.py', output / 'libexec/nav.py')
    (output / 'bin/app').chmod(0o755)
    if (HERE / 'ui').is_dir():
        shutil.copytree(HERE / 'ui', output / 'ui')
    if map_file is not None and CONFIGS[version]['map_enabled']:
        shutil.copy2(map_file, output / 'map.json')
    (output / 'ivi.json').write_text(json.dumps(CONFIGS[version]))
    return output
