"""Fail CI when tests that must run were silently skipped.

A green run with 47 skipped Qt tests proves nothing about Qt. This reads a JUnit
file, prints skips grouped by reason, and exits 1 if any skip reason matches
--forbid (a regex) or the total exceeds --max-skipped.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def skipped(junit: Path) -> list[tuple[str, str]]:
    found = []
    for case in ET.parse(junit).getroot().iter('testcase'):
        node = case.find('skipped')
        if node is not None:
            found.append((f"{case.get('classname', '')}::{case.get('name', '')}",
                          node.get('message') or (node.text or '').strip()))
    return found


def check(junit: Path, forbid: str | None, max_skipped: int | None) -> tuple[list[str], list[tuple[str, str]]]:
    items = skipped(junit)
    problems = []
    if forbid:
        pattern = re.compile(forbid)
        bad = [name for name, reason in items if pattern.search(reason)]
        if bad:
            problems.append(f'{len(bad)} test(s) skipped for a forbidden reason /{forbid}/: ' + ', '.join(bad[:5])
                            + (' ...' if len(bad) > 5 else ''))
    if max_skipped is not None and len(items) > max_skipped:
        problems.append(f'{len(items)} skipped tests exceed the allowed {max_skipped}')
    return problems, items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('junit', type=Path)
    parser.add_argument('--forbid', help='regex; a skip whose reason matches fails the check')
    parser.add_argument('--max-skipped', type=int)
    args = parser.parse_args(argv)
    problems, items = check(args.junit, args.forbid, args.max_skipped)
    print(f'skipped tests: {len(items)}')
    for reason, count in Counter(reason for _, reason in items).most_common():
        print(f'  {count:3d} x {reason}')
    for problem in problems:
        print(f'ERROR: {problem}', file=sys.stderr)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
