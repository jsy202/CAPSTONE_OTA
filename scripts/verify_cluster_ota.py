#!/usr/bin/env python3
"""Run the Cluster OTA verification gates and write traceable evidence.

The single source of truth is docs/verification/verification_measures.json:
requirement -> test case -> automated test node(s) -> gate. This script runs
the full pytest suite once (JUnit XML), maps every case to its requirement,
checks that protected OTA code is unchanged against the merge base, and prints
a PASS/FAIL verdict per requirement and per gate. It never re-implements test
logic: results come only from pytest and from evidence files.

Rules that keep the verdict honest:
  * a referenced test that does not exist is FAIL (broken traceability)
  * a skipped test is NOT_EXECUTED, never PASS
  * a gate PASSes only with executed PASSes and no FAIL or NOT_EXECUTED; any
    NOT_EXECUTED member makes it INCOMPLETE; a required gate without measures FAILs
  * results: PASS (exit 0), INCOMPLETE (exit 2), FAIL (exit 1); FAIL wins
  * hardware measures are PENDING_HARDWARE and are listed, not passed

usage: scripts/verify_cluster_ota.py [--qt-dir DIR] [--upstream-dir DIR]
                                     [--out DIR] [--snapshot DIR] [--base-ref REF]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEASURES = ROOT / "docs" / "verification" / "verification_measures.json"


def load_measures(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_junit(path: Path) -> dict[str, list[str]]:
    """Map 'tests/x/test_y.py::test_name' to the outcomes of all its cases."""
    outcomes: dict[str, list[str]] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "").split("[")[0]
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        else:
            outcome = "passed"
        outcomes.setdefault(node, []).append(outcome)
    return outcomes


def junit_totals(path: Path) -> dict:
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in ET.parse(path).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    return totals


def _check_evidence(check: dict, root: Path) -> str | None:
    path = root / check["path"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"evidence missing or unreadable: {check['path']}"
    for key, expected in check.get("expect", {}).items():
        if data.get(key) != expected:
            return f"evidence {check['path']}: {key}={data.get(key)!r}, expected {expected!r}"
    recorded = data.get("source_sha256", {})
    for key, source in check.get("sha256_of", {}).items():
        try:
            current = hashlib.sha256((root / source).read_bytes()).hexdigest()
        except OSError:
            return f"evidence source missing: {source}"
        if recorded.get(key) != current:
            return f"evidence {check['path']} is stale for {source} (re-run the capture)"
    return None


def measure_result(measure: dict, junit: dict[str, list[str]], root: Path) -> dict:
    result = {key: measure.get(key) for key in ("vr", "tc", "title", "gate", "kind")}
    if measure["kind"] == "hardware":
        return {**result, "status": "PENDING_HARDWARE", "detail": "requires the two-Pi rig", "cases": {}}
    cases, problems, skipped = {}, [], []
    for node in measure["tests"]:
        outcomes = junit.get(node)
        if not outcomes:
            problems.append(f"missing test {node}")
            continue
        cases[node] = outcomes
        if any(o == "failed" for o in outcomes):
            problems.append(f"failed {node}")
        elif any(o == "skipped" for o in outcomes):
            skipped.append(node)
    for check in measure.get("evidence_checks", []):
        problem = _check_evidence(check, root)
        if problem:
            problems.append(problem)
    if problems:
        return {**result, "status": "FAIL", "detail": "; ".join(problems), "cases": cases}
    if skipped:
        return {**result, "status": "NOT_EXECUTED", "detail": "skipped: " + ", ".join(skipped), "cases": cases}
    return {**result, "status": "PASS", "detail": f"{sum(len(o) for o in cases.values())} cases passed", "cases": cases}


EXIT_CODES = {"PASS": 0, "INCOMPLETE": 2, "FAIL": 1}

GATE_TITLES = {
    "G1": "Unit verification (SWE.4)",
    "G2": "Status propagation / component integration (SWE.5)",
    "G3": "Normal OTA commit state transition (SWE.6)",
    "G4": "Fault-injection rollback (SWE.6)",
    "G5": "Recovery verification (SWE.6)",
    "G6": "Full regression + change impact",
    "G7": "Privilege / security regression",
    "A1": "Central unit verification",
    "A2": "Cluster IPC / model integration",
    "A3": "Application protocol conformance",
    "A4": "Normal compatibility verification (real apps)",
    "A5": "Semantic fault detection (real fault build)",
    "A6": "Whole-vehicle rollback (real apps)",
    "A7": "Recovery verification (real apps)",
    "A8": "Application security / privilege regression",
    "A9": "Full regression + change impact (application contracts)",
}
REGRESSION_GATES = {"G6", "A9"}


def _default_gates(results: list[dict]) -> list[str]:
    families = {r["gate"][0] for r in results if r["gate"] in GATE_TITLES}
    return [g for g in GATE_TITLES if g[0] in families]


def evaluate_gates(results: list[dict], *, protected_diff_empty: bool, full_regression: dict,
                   required_gates: list[str] | None = None) -> dict:
    """required_gates (from the measures file) are all mandatory; a required gate
    with no executed PASS fails. Without it, the gate families present are evaluated."""
    gates = []
    for gate_id in (required_gates if required_gates is not None else _default_gates(results)):
        title = GATE_TITLES.get(gate_id, gate_id)
        members = [r for r in results if r["gate"] == gate_id]
        if gate_id in REGRESSION_GATES:
            ok = (full_regression["failures"] == 0 and full_regression["errors"] == 0
                  and full_regression["tests"] > 0 and protected_diff_empty)
            detail = (f"{full_regression['tests']} tests, {full_regression['failures']} failures, "
                      f"{full_regression['errors']} errors, {full_regression['skipped']} skipped; "
                      f"protected OTA paths {'unchanged' if protected_diff_empty else 'CHANGED'}")
            failed_members = [r["vr"] for r in members if r["status"] == "FAIL"]
            status = "PASS" if ok and not failed_members else "FAIL"
        else:
            failed_members = [r["vr"] for r in members if r["status"] == "FAIL"]
            executed = [r for r in members if r["status"] == "PASS"]
            not_executed = [r for r in members if r["status"] == "NOT_EXECUTED"]
            if failed_members or not (executed or not_executed):
                status = "FAIL"          # a failure, or a required gate with no measures at all
            elif not_executed:
                status = "INCOMPLETE"    # nothing failed, but something could not run here
            else:
                status = "PASS"
            detail = f"{len(executed)} PASS, {len(failed_members)} FAIL, " \
                     f"{sum(r['status'] == 'NOT_EXECUTED' for r in members)} NOT_EXECUTED"
        gates.append({"id": gate_id, "title": title, "status": status, "detail": detail, "failed": failed_members})
    return {
        "gates": gates,
        "result": ("FAIL" if any(g["status"] == "FAIL" for g in gates)
                   else "INCOMPLETE" if any(g["status"] == "INCOMPLETE" for g in gates) else "PASS"),
        "not_executed": [r["vr"] for r in results if r["status"] == "NOT_EXECUTED"],
        "pending_hardware": [r["vr"] for r in results if r["status"] == "PENDING_HARDWARE"],
    }


def protected_paths_unchanged(paths: list[str], base_ref: str) -> tuple[bool, str]:
    base = subprocess.run(["git", "merge-base", "HEAD", base_ref], cwd=ROOT, capture_output=True, text=True)
    if base.returncode != 0:
        return False, f"cannot find merge base with {base_ref}"
    merge_base = base.stdout.strip()
    diff = subprocess.run(["git", "diff", "--name-only", merge_base, "--", *paths],
                          cwd=ROOT, capture_output=True, text=True)
    changed = [line for line in diff.stdout.splitlines() if line]
    status = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    changed = sorted(set(changed) | {line[3:] for line in status.stdout.splitlines() if line})
    return not changed and diff.returncode == 0, merge_base[:12] + (": " + ", ".join(changed) if changed else "")


def _matrix(data: dict, results: list[dict]) -> str:
    by_vr = {r["vr"]: r for r in results}
    lines = ["# Traceability Matrix (generated)", "",
             "Generated by `scripts/verify_cluster_ota.py` from `docs/verification/verification_measures.json` "
             "and the JUnit results of the same run. Do not edit by hand.", "",
             "| Requirement | Test case | Level | Gate | Automated test(s) | Result |", "|---|---|---|---|---|---|"]
    for m in data["measures"]:
        r = by_vr[m["vr"]]
        tests = "<br>".join(f"`{t}`" for t in m["tests"]) or "—"
        if m.get("evidence_checks"):
            tests += "<br>" + "<br>".join(f"evidence `{c['path']}`" for c in m["evidence_checks"])
        lines.append(f"| {m['vr']} {m['title']} | {m['tc']} | {m['level']} | {m['gate']} | {tests} | **{r['status']}** |")
    lines += ["", "## Reverse trace: automated test -> requirement", "", "| Automated test | Requirement(s) |", "|---|---|"]
    reverse: dict[str, list[str]] = {}
    for m in data["measures"]:
        for t in m["tests"]:
            reverse.setdefault(t, []).append(m["vr"])
    for test in sorted(reverse):
        lines.append(f"| `{test}` | {', '.join(sorted(set(reverse[test])))} |")
    return "\n".join(lines) + "\n"


def requirements_markdown(data: dict) -> str:
    """Render the verification requirements document from the measures file."""
    lines = ["# Verification Requirements (generated)", "",
             "Generated from `docs/verification/verification_measures.json` by "
             "`scripts/verify_cluster_ota.py --write-requirements`; a unit test fails if this file drifts.", "",
             f"Reference: {data['references']}", "",
             "| ID | Requirement | Level | Gate | Kind |", "|---|---|---|---|---|"]
    lines += [f"| {m['vr']} | {m['title']} | {m['level']} | {m['gate']} | {m['kind']} |" for m in data["measures"]]
    for m in data["measures"]:
        lines += ["", f"## {m['vr']} — {m['title']}", "",
                  f"- **Test case:** {m['tc']}",
                  f"- **Verification level:** {m['level']}" + (f" (ISO 26262-6: {m['iso26262_6']})" if m.get("iso26262_6") else ""),
                  f"- **Verification method:** {m['method']}",
                  f"- **Techniques:** {', '.join(m['techniques'])}",
                  f"- **Precondition:** {m['precondition']}",
                  f"- **Expected result:** {m['expected']}",
                  f"- **Pass/Fail criterion:** {m['pass_fail']}",
                  f"- **Gate:** {m['gate']}"]
        if m.get("r156_concepts"):
            lines.append(f"- **UN R156 concept referenced:** {'; '.join(m['r156_concepts'])}")
        tests = [f"`{t}`" for t in m["tests"]] + [f"evidence `{c['path']}` expects {json.dumps(c['expect'])}"
                                                  for c in m.get("evidence_checks", [])]
        lines.append("- **Automated test / evidence:** " + ("; ".join(tests) if tests else "none — manual hardware validation"))
    return "\n".join(lines) + "\n"


def _highlights(evidence_dir: Path, vr: str) -> list[str]:
    path = evidence_dir / f"{vr}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    keys = ("injected_fault", "detected", "display_sequence", "final_slots", "recovery_verification_passed",
            "signed_bundle_cluster_version", "displayed_trial_versions", "journal_mode", "after_boot")
    return [f"{key}: {json.dumps(data[key], ensure_ascii=False)}" for key in keys if key in data]


def environment_summary(qt_dir: Path | None, upstream_dir: Path | None) -> dict:
    """Describe the run environment without recording local filesystem paths."""
    qt_version = None
    if qt_dir:
        try:
            qt_version = subprocess.run([str(Path(qt_dir) / "bin" / "qmake"), "-query", "QT_VERSION"],
                                        capture_output=True, text=True, timeout=30).stdout.strip() or "unknown"
        except OSError:
            qt_version = "unknown"
    apps = [name for name, key in (("normal", "CAPSTONE_CLUSTER_APP"), ("fault", "CAPSTONE_CLUSTER_FAULT_APP"))
            if os.environ.get(key)]
    return {"python": sys.version.split()[0], "platform": sys.platform, "qt": qt_version,
            "upstream_import": "provided" if upstream_dir else None,
            "cluster_apps": "+".join(apps) if apps else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qt-dir", type=Path, default=os.environ.get("CAPSTONE_QT_DIR"))
    parser.add_argument("--upstream-dir", type=Path, default=os.environ.get("CAPSTONE_UPSTREAM_DIR"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--snapshot", type=Path, help="also copy summary, matrix and results here")
    parser.add_argument("--base-ref", default=os.environ.get("CAPSTONE_BASE_REF", "main"))
    parser.add_argument("--write-requirements", action="store_true",
                        help="regenerate docs/verification/REQUIREMENTS.md and exit")
    args = parser.parse_args(argv)

    data = load_measures(MEASURES)
    if args.write_requirements:
        (MEASURES.parent / "REQUIREMENTS.md").write_text(requirements_markdown(data))
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or ROOT / "verification-results" / stamp
    evidence = out / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CAPSTONE_EVIDENCE_DIR=str(evidence))
    for name, value in (("CAPSTONE_QT_DIR", args.qt_dir), ("CAPSTONE_UPSTREAM_DIR", args.upstream_dir)):
        if value:
            env[name] = str(value)
        else:
            env.pop(name, None)

    junit_path = out / "junit.xml"
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit_path}"]
    print("=== CAPSTONE OTA Verification ===")
    print(f"run: {' '.join(command[2:])}")
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    (out / "pytest.log").write_text(run.stdout + run.stderr)
    if not junit_path.exists():
        print(run.stdout[-3000:] + run.stderr[-3000:])
        print("VERIFICATION RESULT: FAIL (pytest produced no results)")
        return 1

    junit = parse_junit(junit_path)
    results = [measure_result(m, junit, ROOT) for m in data["measures"]]
    unchanged, impact = protected_paths_unchanged(data["protected_paths"], args.base_ref)
    totals = junit_totals(junit_path)
    report = evaluate_gates(results, protected_diff_empty=unchanged, full_regression=totals,
                            required_gates=[g["id"] for g in data["gates"]])

    print()
    for gate in report["gates"]:
        print(f"--- {gate['id']} {gate['title']}")
        for r in (r for r in results if r["gate"] == gate["id"]):
            print(f"[{r['status']}] {r['vr']} {r['title']}")
            if r["status"] != "PASS":
                print(f"       {r['detail']}")
            for line in _highlights(evidence, r["vr"]):
                print(f"       {line}")
        if gate["id"] in REGRESSION_GATES:
            print(f"[{gate['status']}] Full regression and change impact: {gate['detail']}")
            print(f"       merge base {impact}")
        print(f"    gate {gate['id']}: {gate['status']} ({gate['detail']})")
    print()
    for r in results:
        if r["status"] == "PENDING_HARDWARE":
            print(f"[PENDING HARDWARE VALIDATION] {r['vr']} {r['title']}")
    for r in results:
        if r["status"] == "NOT_EXECUTED":
            print(f"[NOT EXECUTED] {r['vr']} {r['title']} ({r['detail']})")
    print()
    if report["result"] == "INCOMPLETE":
        print("VERIFICATION RESULT: INCOMPLETE (measures NOT EXECUTED in this environment; see above)")
    else:
        print(f"VERIFICATION RESULT: {report['result']}")

    payload = {"schema_version": 1, "generated_at": stamp, "result": report["result"], "gates": report["gates"],
               "not_executed": report["not_executed"], "pending_hardware": report["pending_hardware"],
               "full_regression": totals, "change_impact": {"protected_paths_unchanged": unchanged, "detail": impact},
               "environment": environment_summary(args.qt_dir, args.upstream_dir),
               "measures": results}
    (out / "results.json").write_text(json.dumps(payload, indent=2))
    (out / "TRACEABILITY_MATRIX.md").write_text(_matrix(data, results))
    summary = [f"# Verification Summary ({stamp})", "", f"**VERIFICATION RESULT: {report['result']}**", "",
               "| Gate | Status | Detail |", "|---|---|---|"]
    summary += [f"| {g['id']} {g['title']} | {g['status']} | {g['detail']} |" for g in report["gates"]]
    summary += ["", "| Requirement | Status | Detail |", "|---|---|---|"]
    summary += [f"| {r['vr']} {r['title']} | {r['status']} | {r['detail']} |" for r in results]
    (out / "summary.md").write_text("\n".join(summary) + "\n")
    if args.snapshot:
        args.snapshot.mkdir(parents=True, exist_ok=True)
        for name in ("results.json", "summary.md", "TRACEABILITY_MATRIX.md"):
            shutil.copy2(out / name, args.snapshot / name)
        shutil.copytree(evidence, args.snapshot / "evidence", dirs_exist_ok=True)
    print(f"evidence: {out}")
    return EXIT_CODES[report["result"]]


if __name__ == "__main__":
    sys.exit(main())
