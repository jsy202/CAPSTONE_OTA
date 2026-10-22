import json
from pathlib import Path
import shutil
import subprocess


def test_example_check_runs_real_initialization_and_rejects_bad_config(tmp_path):
    source = Path(__file__).parents[1] / "examples/central"
    assert (source / "bin/app").is_file(), "example application is missing"
    payload = tmp_path / "app"
    shutil.copytree(source, payload)
    app = payload / "bin/app"
    good = subprocess.run([str(app), "--check"], cwd="/", capture_output=True, text=True)
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["period_ms"] == 100
    (payload / "config.json").write_text('{"period_ms":0,"label":"bad"}')
    bad = subprocess.run([str(app), "--check"], capture_output=True, text=True)
    assert bad.returncode != 0
