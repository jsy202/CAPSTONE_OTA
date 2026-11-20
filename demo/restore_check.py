"""Post-restore re-check for the drill: does the slot hold exactly what was committed?

Used as the external check command (the slot path is appended as the last argument). It reads the
device's own committed record and compares tree hashes, so a restore of anything else fails. On real
boards the re-check is the Pi3 probation instead; this is the stand-in when no Verifier board exists.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from capstone_ota.bundle import tree_hash


def verdict(slot: Path) -> dict:
    try:
        committed = json.loads((slot.parent.parent / "state.json").read_text())["committed"]
        observed = tree_hash(slot)
        return {"passed": bool(committed) and observed == committed["tree_sha256"],
                "expected_tree_sha256": committed and committed["tree_sha256"], "observed_tree_sha256": observed}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"passed": False, "error": str(exc)}


if __name__ == "__main__":
    print(json.dumps(verdict(Path(sys.argv[-1]))))
