import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from capstone_ota.bundle import tree_hash

DEMO = Path(__file__).parents[1] / "demo"


def make_slot(root, publisher="v2"):
    assert (DEMO / "central_app.py").is_file(), "virtual Central actor is missing"
    slot = root / "payload"
    (slot / "bin").mkdir(parents=True)
    shutil.copyfile(DEMO / "central_app.py", slot / "bin/app")
    shutil.copyfile(DEMO / f"publisher_{publisher}.py", slot / "publisher.py")
    shutil.copyfile(DEMO / "wire.py", slot / "wire.py")
    (slot / "release.txt").write_text(publisher)
    (slot / "bin/app").chmod(0o755)
    return slot


def verifier():
    assert importlib.util.find_spec("demo.verify") is not None, "external verifier is missing"
    return importlib.import_module("demo.verify")


def test_wire_crc_uses_known_smbus_check_value():
    assert importlib.util.find_spec("demo.wire") is not None, "CAN payload codec is missing"
    wire = importlib.import_module("demo.wire")
    assert wire.crc8(b"123456789") == 0xF4
    frame = wire.encode(400, 250)
    assert frame["id"] == 0x200
    assert bytes.fromhex(frame["data"])[:7] == bytes([1, 144, 5, 220, 3, 0, 250])
    assert wire.decode(frame) == {"speed": 400, "counter": 250}
    corrupt = dict(frame, data=frame["data"][:-2] + "00")
    with pytest.raises(ValueError):
        wire.decode(corrupt)


def test_v2_survives_wrap_and_reconnect_using_separate_real_processes(tmp_path):
    slot = make_slot(tmp_path)
    before = tree_hash(slot)
    report = verifier().verify(slot)
    assert report["passed"] is True
    assert report["initialization_ok"] is True
    assert report["frame_format_ok"] is True
    assert report["accepted_before"] == 8
    assert report["accepted_after"] == 6
    assert report["post_counters"] == [2, 3, 4, 5, 6, 7]
    assert report["last_model_speed"] == 650
    assert len(set(report["actor_pids"].values())) == 3
    assert tree_hash(slot) == before


def test_v3_initializes_and_sends_valid_frames_but_receiver_stalls_after_reconnect(tmp_path):
    slot = make_slot(tmp_path, "v3")
    report = verifier().verify(slot)
    assert report["initialization_ok"] is True
    assert report["frame_format_ok"] is True
    assert report["accepted_before"] == 8
    assert report["accepted_after"] == 0
    assert report["post_counters"] == [250, 251, 252, 253, 254, 255]
    assert report["last_model_speed"] == 470
    assert report["passed"] is False
    assert report["error"] == "RECONNECT_SEQUENCE_INCOMPATIBLE"


def test_malformed_actor_output_cannot_be_accepted(tmp_path):
    slot = make_slot(tmp_path)
    (slot / "bin/app").write_text('#!/usr/bin/env python3\nprint("{}")\n')
    report = verifier().verify(slot)
    assert report["passed"] is False
    assert report["error"]


@pytest.mark.parametrize("body", ["import time; time.sleep(10)",
                                   'import os\nwhile True: os.write(1, b"x" * 4096)'])
def test_hanging_or_flooding_actor_is_bounded(tmp_path, body):
    slot = make_slot(tmp_path)
    (slot / "bin/app").write_text("#!/usr/bin/env python3\n" + body + "\n")
    started = time.monotonic()
    report = verifier().verify(slot, timeout=0.2)
    assert report["passed"] is False
    assert time.monotonic() - started < 2


def test_fixed_cluster_accepts_natural_counter_wrap():
    module = verifier()
    wire = importlib.import_module("demo.wire")
    frames = [wire.encode(speed, counter) for speed, counter in [(400, 254), (410, 255), (420, 0), (430, 1)]]
    rows, _pid = module.exchange([sys.executable, str(DEMO / "cluster.py")], frames)
    assert [row["accepted"] for row in rows] == [True, True, True, True]
    assert [row["counter"] for row in rows] == [254, 255, 0, 1]
    assert [row["speed"] for row in rows] == [400, 410, 420, 430]


def test_verifier_reaps_child_workers_even_if_actor_parent_already_exited(tmp_path):
    module = verifier()
    script, pidfile = tmp_path / "actor.py", tmp_path / "child.pid"
    script.write_text("import subprocess,sys\nfrom pathlib import Path\n"
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                      f"Path({str(pidfile)!r}).write_text(str(child.pid))\n")
    try:
        with pytest.raises(ValueError, match="timeout"):
            module.exchange([sys.executable, str(script)], [], timeout=0.2)
        child = int(pidfile.read_text())
        status = Path(f"/proc/{child}/stat")
        deadline = time.monotonic() + 0.5
        while status.exists() and status.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not status.exists() or status.read_text().split()[2] == "Z"
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 9)
            except ProcessLookupError:
                pass


def test_full_virtual_chain_distinguishes_init_compatibility_and_signed_slot_swap(tmp_path):
    assert importlib.util.find_spec("demo.run") is not None, "signed OTA virtual runner is missing"
    run = importlib.import_module("demo.run").run
    output = tmp_path / "bench"
    result = run(output)
    assert result["baseline"]["result"] == "committed"
    assert result["baseline"]["version"] == "3.0.0"
    normal = result["normal"]
    assert normal["status"]["result"] == "rolled_back"
    assert normal["status"]["version"] == "2.0.0"
    assert normal["status"]["min_svn"] == 2
    candidate = next(check for check in normal["checks"] if check["payload_label"] == "v3")
    assert candidate["report"]["initialization_ok"] is True
    assert candidate["report"]["frame_format_ok"] is True
    assert candidate["report"]["accepted_after"] == 0
    assert normal["checks"][-1]["report"]["passed"] is True
    attack = result["attack"]
    assert attack["old_signature_valid"] is True
    assert attack["old_release"]["version"] == "1.0.0"
    assert attack["trusted_journal_unchanged_during_swap"] is True
    assert attack["status"]["result"] == "recovery_failed"
    assert attack["status"]["min_svn"] == 2
    assert attack["status"]["active_slot"] == "B"
    assert "hash" in attack["blocked_reason"]
    assert not any(check["payload_label"] == "v1" for check in attack["checks"])
    assert json.loads((output / "result.json").read_text()) == result
    original = (output / "result.json").read_bytes()
    with pytest.raises(FileExistsError):
        run(output)
    assert (output / "result.json").read_bytes() == original


def test_virtual_demo_cli_runs_in_fresh_workspace(tmp_path):
    output = tmp_path / "cli-demo"
    process = subprocess.run([sys.executable, "-m", "demo.run", "--output", str(output)],
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    result = json.loads((output / "result.json").read_bytes())
    assert result["normal"]["status"]["result"] == "rolled_back"
    assert result["attack"]["status"]["result"] == "recovery_failed"


def test_negative_verdict_preserves_evidence_but_nonzero_cannot_approve():
    module = verifier()
    command = [sys.executable, "-c", 'print(\'{"passed":false,"error":"INCOMPATIBLE"}\');raise SystemExit(1)']
    rows, _pid = module.exchange(command, [], allow_negative_verdict=True)
    assert rows == [{"passed": False, "error": "INCOMPATIBLE"}]
    command = [sys.executable, "-c", 'print(\'{"passed":true}\');raise SystemExit(1)']
    with pytest.raises(ValueError, match="unsuccessfully"):
        module.exchange(command, [], allow_negative_verdict=True)


def test_modified_v1_archive_is_not_misreported_as_an_authentic_old_image(tmp_path, monkeypatch):
    module = importlib.import_module("demo.run")
    original = module.pack
    def modified(payload, output, version, svn, key):
        result = original(payload, output, version, svn, key)
        if version == "1.0.0":
            archive = output / "app.tar.gz"
            archive.write_bytes(archive.read_bytes() + b"modified after signing")
        return result
    monkeypatch.setattr(module, "pack", modified)
    with pytest.raises(ValueError, match="hash"):
        module.run(tmp_path / "modified-bench")
