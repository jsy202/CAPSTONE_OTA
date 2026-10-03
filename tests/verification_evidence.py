"""Machine-readable verification evidence written by tests on request.

When CAPSTONE_EVIDENCE_DIR is set (scripts/verify_cluster_ota.py sets it), a
test may record what it observed for a verification requirement. Records are
written as <dir>/<requirement_id>.json; without the variable this is a no-op,
so normal pytest runs are unaffected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def record(requirement_id: str, data: dict) -> None:
    directory = os.environ.get("CAPSTONE_EVIDENCE_DIR")
    if not directory:
        return
    path = Path(directory) / f"{requirement_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"requirement": requirement_id, **data}, indent=2, sort_keys=True, default=str))
