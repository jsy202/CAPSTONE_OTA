"""Link docs/TEST_CASES.md (TC ID, requirement) to automated test functions and the latest JUnit result.

Checks two kinds of drift: a test name written in the spec that no longer exists in tests/,
and a physical-bench test that has no TC row. Result column meaning:
  pass / fail      executed in the given JUnit run
  skip             collected but not run (physical-bench tests skip unless --hardware-config is given)
  not-run          function exists, no result in the given JUnit file
  no-function      the spec row names no function (manual / not yet automated)
  MISSING          the spec names a function that does not exist  -> --check fails
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ID = re.compile(r'^([A-Z]+(?:-[A-Za-z0-9]+)+)\s*(?:/\s*(.*))?$')
SAME_AS_ABOVE = ('동일', '파라미터')
TEST = re.compile(r'`(test_[A-Za-z0-9_]+)(?:\[[^`]*\])?`')


def parse_spec(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        match = ID.match(cells[0]) if cells else None
        if not match:
            continue
        functions = sorted(set(TEST.findall(cells[-1])))
        if not functions and rows and any(word in cells[-1] for word in SAME_AS_ABOVE):
            functions = rows[-1]['functions']  # "동일 시험" / "<param> 파라미터": same test function as the row above
        rows.append({'id': match.group(1), 'requirement': (match.group(2) or '').strip(), 'functions': functions})
    return rows


def discover(tests: Path) -> dict[str, str]:
    found = {}
    for file in sorted(tests.rglob('test_*.py')):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                found.setdefault(node.name, (file.relative_to(ROOT) if file.is_relative_to(ROOT) else file).as_posix())
    return found


def results(junit: Path | None) -> dict[str, str]:
    if not junit:
        return {}
    by_function: dict[str, set[str]] = {}
    for case in ET.parse(junit).getroot().iter('testcase'):
        name = re.sub(r'\[.*\]$', '', case.get('name', ''))
        state = ('fail' if case.find('failure') is not None or case.find('error') is not None
                 else 'skip' if case.find('skipped') is not None else 'pass')
        by_function.setdefault(name, set()).add(state)
    return {n: ('fail' if 'fail' in s else 'pass' if 'pass' in s else 'skip') for n, s in by_function.items()}


def build(spec: Path, tests: Path, junit: Path | None) -> dict:
    functions, outcome = discover(tests), results(junit)
    rows, mentioned = [], set()
    for row in parse_spec(spec):
        states = []
        for name in row['functions']:
            mentioned.add(name)
            states.append('MISSING' if name not in functions else outcome.get(name, 'not-run'))
        status = ('no-function' if not states else 'MISSING' if 'MISSING' in states else 'fail' if 'fail' in states
                  else 'not-run' if 'not-run' in states else 'skip' if 'skip' in states else 'pass')
        rows.append(dict(row, status=status, files=sorted({functions[n] for n in row['functions'] if n in functions})))
    hardware = sorted(n for n, f in functions.items() if '/hardware/' in f)
    return {'rows': rows, 'uncovered_hardware_tests': [n for n in hardware if n not in mentioned]}


def markdown(report: dict) -> str:
    out = ['| TC ID | 요구사항 | 자동화 함수 | 결과 |', '| --- | --- | --- | --- |']
    for row in report['rows']:
        funcs = ', '.join(f'`{f}`' for f in row['functions']) or '—'
        out.append(f"| {row['id']} | {row['requirement'] or '—'} | {funcs} | {row['status']} |")
    if report['uncovered_hardware_tests']:
        out += ['', '**TC 행이 없는 실보드 테스트:** ' + ', '.join(f'`{n}`' for n in report['uncovered_hardware_tests'])]
    return '\n'.join(out) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--spec', type=Path, default=ROOT / 'docs/TEST_CASES.md')
    parser.add_argument('--tests', type=Path, default=ROOT / 'tests')
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--markdown', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--check', action='store_true', help='exit 1 if the spec names a missing test function')
    args = parser.parse_args(argv)
    report = build(args.spec, args.tests, args.junit)
    text = markdown(report)
    if args.markdown:
        args.markdown.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(text)
    missing = [r['id'] for r in report['rows'] if r['status'] == 'MISSING']
    if missing:
        print('ERROR: spec names test functions that do not exist for: ' + ', '.join(missing), file=sys.stderr)
    return 1 if (args.check and missing) else 0


if __name__ == '__main__':
    sys.exit(main())
