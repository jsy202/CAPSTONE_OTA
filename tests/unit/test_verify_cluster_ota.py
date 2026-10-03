"""Unit verification of the verification-gate evaluator (scripts/verify_cluster_ota.py)."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MEASURES = ROOT / "docs" / "verification" / "verification_measures.json"


def _module():
    spec = importlib.util.spec_from_file_location("verify_cluster_ota", ROOT / "scripts" / "verify_cluster_ota.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _module()

JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="5">
<testcase classname="tests.unit.test_a" name="test_ok" />
<testcase classname="tests.unit.test_a" name="test_param[x]" />
<testcase classname="tests.unit.test_a" name="test_param[y]" />
<testcase classname="tests.unit.test_a" name="test_bad"><failure message="boom" /></testcase>
<testcase classname="tests.integration.test_b" name="test_optin"><skipped message="no qt" /></testcase>
</testsuite></testsuites>"""


@pytest.fixture
def junit(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    return V.parse_junit(path)


def measure(tests=(), gate="G1", kind="automated", evidence=None):
    return {"vr": "VR-X", "tc": "TC-X", "title": "x", "gate": gate, "kind": kind,
            "tests": list(tests), "evidence_checks": evidence or []}


def test_parse_junit_maps_classname_to_node_and_groups_parameters(junit):
    assert junit["tests/unit/test_a.py::test_ok"] == ["passed"]
    assert junit["tests/unit/test_a.py::test_param"] == ["passed", "passed"]
    assert junit["tests/unit/test_a.py::test_bad"] == ["failed"]
    assert junit["tests/integration/test_b.py::test_optin"] == ["skipped"]


def test_measure_passes_only_when_every_case_passed(junit):
    assert V.measure_result(measure(["tests/unit/test_a.py::test_ok", "tests/unit/test_a.py::test_param"]), junit, ROOT)["status"] == "PASS"


def test_measure_with_one_failed_case_fails(junit):
    assert V.measure_result(measure(["tests/unit/test_a.py::test_ok", "tests/unit/test_a.py::test_bad"]), junit, ROOT)["status"] == "FAIL"


def test_missing_test_node_fails_never_passes(junit):
    result = V.measure_result(measure(["tests/unit/test_a.py::test_renamed"]), junit, ROOT)
    assert result["status"] == "FAIL" and "missing" in result["detail"]


def test_skipped_test_is_not_executed_never_pass(junit):
    assert V.measure_result(measure(["tests/integration/test_b.py::test_optin"]), junit, ROOT)["status"] == "NOT_EXECUTED"


def test_manual_hardware_measure_is_pending(junit):
    assert V.measure_result(measure(kind="hardware"), junit, ROOT)["status"] == "PENDING_HARDWARE"


def test_evidence_check_requires_expected_values(junit, tmp_path):
    (tmp_path / "e.json").write_text(json.dumps({"failed": False}))
    ok = measure(["tests/unit/test_a.py::test_ok"], evidence=[{"path": "e.json", "expect": {"failed": False}}])
    assert V.measure_result(ok, junit, tmp_path)["status"] == "PASS"
    (tmp_path / "e.json").write_text(json.dumps({"failed": True}))
    assert V.measure_result(ok, junit, tmp_path)["status"] == "FAIL"
    (tmp_path / "e.json").unlink()
    assert V.measure_result(ok, junit, tmp_path)["status"] == "FAIL"


def _gates(statuses, protected=True, regression=None):
    results = [{"vr": f"VR-{i}", "gate": gate, "status": status} for i, (gate, status) in enumerate(statuses)]
    return V.evaluate_gates(results, protected_diff_empty=protected,
                            full_regression=regression or {"tests": 10, "failures": 0, "errors": 0, "skipped": 0})


GATES = [("G1", "PASS"), ("G2", "PASS"), ("G3", "PASS"), ("G4", "PASS"), ("G5", "PASS"), ("G7", "PASS")]


def test_all_gates_pass_gives_pass():
    assert _gates(GATES)["result"] == "PASS"


def test_one_failed_measure_fails_its_gate_and_the_result():
    report = _gates(GATES + [("G4", "FAIL")])
    assert report["result"] == "FAIL"
    assert [g["id"] for g in report["gates"] if g["status"] == "FAIL"] == ["G4"]


def test_not_executed_measure_makes_the_result_incomplete_never_pass():
    report = _gates(GATES + [("G2", "NOT_EXECUTED")])
    assert report["result"] == "INCOMPLETE"
    assert report["not_executed"] == ["VR-6"]
    assert [g["status"] for g in report["gates"] if g["id"] == "G2"] == ["INCOMPLETE"]


def test_fail_outranks_incomplete():
    assert _gates(GATES + [("G2", "NOT_EXECUTED"), ("G4", "FAIL")])["result"] == "FAIL"


def test_exit_codes_distinguish_pass_incomplete_fail():
    assert (V.EXIT_CODES["PASS"], V.EXIT_CODES["INCOMPLETE"], V.EXIT_CODES["FAIL"]) == (0, 2, 1)


def test_gate_with_only_not_executed_measures_fails():
    report = _gates([(g, s) for g, s in GATES if g != "G3"] + [("G3", "NOT_EXECUTED")])
    assert report["result"] == "FAIL"


def test_full_regression_failure_or_protected_change_fails_g6():
    assert _gates(GATES, regression={"tests": 10, "failures": 1, "errors": 0, "skipped": 0})["result"] == "FAIL"
    assert _gates(GATES, protected=False)["result"] == "FAIL"


def test_hardware_pending_is_reported_separately_and_does_not_fail():
    report = _gates(GATES + [("HW", "PENDING_HARDWARE")])
    assert report["result"] == "PASS" and report["pending_hardware"] == ["VR-6"]


# --- the real measures file (traceability integrity) -------------------------

def test_every_measure_test_node_exists_in_the_suite():
    measures = V.load_measures(MEASURES)
    collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                               capture_output=True, text=True).stdout
    nodes = {line.split("[")[0] for line in collected.splitlines() if "::" in line}
    referenced = {node for m in measures["measures"] for node in m["tests"]}
    assert referenced and referenced <= nodes, sorted(referenced - nodes)


def test_every_requirement_has_a_measure_and_every_gate_is_defined():
    data = V.load_measures(MEASURES)
    gates = {g["id"] for g in data["gates"]}
    for m in data["measures"]:
        assert m["gate"] in gates | {"HW"}, m["vr"]
        assert m["kind"] in {"automated", "conditional", "hardware"}, m["vr"]
        for key in ("vr", "tc", "title", "level", "method", "techniques", "precondition", "expected", "pass_fail"):
            assert m.get(key), (m["vr"], key)
        if m["kind"] != "hardware":
            assert m["tests"] or m["evidence_checks"], m["vr"]


def test_protected_change_is_reported_once_and_fails(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "core").mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (repo / "core" / "x.py").write_text("a\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    (repo / "core" / "x.py").write_text("b\n")
    git("commit", "-qam", "change")
    (repo / "core" / "x.py").write_text("c\n")  # and an uncommitted edit
    monkeypatch.setattr(V, "ROOT", repo)
    unchanged, detail = V.protected_paths_unchanged(["core"], "main")
    assert unchanged is False
    assert detail.count("core/x.py") == 1


def test_committed_requirements_document_matches_measures():
    expected = V.requirements_markdown(V.load_measures(MEASURES))
    assert (ROOT / "docs" / "verification" / "REQUIREMENTS.md").read_text() == expected, \
        "regenerate with: python3 scripts/verify_cluster_ota.py --write-requirements"


def test_evidence_check_rejects_stale_source_hash(junit, tmp_path):
    import hashlib
    (tmp_path / "src.qml").write_text("v2")
    good = hashlib.sha256(b"v2").hexdigest()
    check = {"path": "e.json", "expect": {"failed": False}, "sha256_of": {"badge": "src.qml"}}
    (tmp_path / "e.json").write_text(json.dumps({"failed": False, "source_sha256": {"badge": good}}))
    m = measure(["tests/unit/test_a.py::test_ok"], evidence=[check])
    assert V.measure_result(m, junit, tmp_path)["status"] == "PASS"
    (tmp_path / "src.qml").write_text("v3")
    result = V.measure_result(m, junit, tmp_path)
    assert result["status"] == "FAIL" and "stale" in result["detail"]
