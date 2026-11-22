import json

from bench import preflight


class Board:
    """A board that answers by what the command contains."""

    def __init__(self, answers, fail=False):
        self.answers, self.fail, self.commands = answers, fail, []

    def run(self, command, **_):
        self.commands.append(command)
        if self.fail:
            raise OSError("no route to host")
        for needle, out in self.answers.items():
            if needle in command:
                return type("R", (), {"stdout": out, "returncode": 0})()
        return type("R", (), {"stdout": "", "returncode": 0})()


STATUS = json.dumps({"version": "3.9.0", "phase": "stable", "active_slot": "B"})


def healthy(tmp_path, code_files="a.py 1\nb.py 2\n"):
    release = tmp_path / "rel"
    (release / "keys").mkdir(parents=True)
    (release / "keys/ivi-private.pem").write_text("x")
    for name in ("v2", "v3"):
        (release / "releases" / name).mkdir(parents=True)
    config = {"release_dir": str(release), "laptop_ip": "10.0.0.9", "baseline_version": "3.9.0",
              "demo_pace": {"download": 10, "install": 10, "park": 40}}
    pi = {"can0": "can0 UP", "echo ok": "ok"}
    boards = {"pi1": Board(dict(pi)), "pi3": Board(dict(pi)),
              "pi2": Board({**pi, "status": STATUS, "hashlib": code_files})}
    return config, boards


def verdicts(results):
    return {name: ok for name, ok, _detail in results}


def test_a_healthy_setup_passes_every_check(tmp_path):
    config, boards = healthy(tmp_path)
    results = preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"])
    assert all(ok for _n, ok, _d in results), results
    assert preflight.exit_code(results) == 0


def test_stale_code_on_pi2_is_caught(tmp_path):
    config, boards = healthy(tmp_path, code_files="a.py 1\nb.py 999\n")
    results = verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"]))
    assert results["Pi2 runs this checkout's code"] is False


def test_an_unreachable_board_fails_without_crashing_the_rest(tmp_path):
    config, boards = healthy(tmp_path)
    boards["pi3"] = Board({}, fail=True)
    results = verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"]))
    assert results["Pi3 reachable"] is False and results["Pi1 reachable"] is True
    assert preflight.exit_code(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"])) == 1


def test_pi2_must_be_stable_at_the_baseline_version(tmp_path):
    config, boards = healthy(tmp_path)
    boards["pi2"].answers["status"] = json.dumps({"version": "3.9.0", "phase": "trial"})
    assert verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"]))["Pi2 IVI stable at 3.9.0"] is False


def test_the_laptop_must_have_the_address_the_boards_report_to(tmp_path):
    config, boards = healthy(tmp_path)
    results = verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["192.168.1.5"]))
    assert results["laptop has laptop_ip 10.0.0.9"] is False


def test_a_missing_demo_pace_is_reported_not_silently_fast(tmp_path):
    config, boards = healthy(tmp_path)
    del config["demo_pace"]
    assert verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"]))["demo pace set (download/install/park)"] is False


def test_the_check_never_changes_anything_on_the_boards(tmp_path):
    config, boards = healthy(tmp_path)
    preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"])
    forbidden = ("rm ", "tee ", "systemctl", "pkill", " mv ", "sudo -n rm", "install ")
    for board in boards.values():
        assert not [c for c in board.commands if any(word in c for word in forbidden)], board.commands


def test_a_file_missing_on_pi2_is_caught(tmp_path):
    config, boards = healthy(tmp_path, code_files="a.py 1\n")
    assert verdicts(preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"]))["Pi2 runs this checkout's code"] is False


def test_a_leftover_file_from_an_older_install_is_reported_but_does_not_fail(tmp_path):
    config, boards = healthy(tmp_path, code_files="a.py 1\nb.py 2\nold_gate.py 7\n")
    results = preflight.check(config, boards, local_files={"a.py": "1", "b.py": "2"}, laptop_addresses=["10.0.0.9"])
    row = next(r for r in results if r[0] == "Pi2 runs this checkout's code")
    assert row[1] is True and "old_gate.py" in row[2]
