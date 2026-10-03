"""Read-only Cluster UI status derived from the application A/B slot journal.

The status is display metadata for the Qt Digital Cluster only. It never
writes slot directories or state.json and is not consulted by any OTA
decision. The trial rule mirrors ZonalAgent.publish_heartbeat: the trial
application runs when trial_slot is set and is the selected slot. State names
are the lowercase ApplicationState members plus "unknown"; anything that
cannot be established fails closed to "unknown", never to a guessed version.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from capstone_ota.common.can_protocol import ApplicationState
from capstone_ota.common.errors import OtaError

from .slots import SlotState


SCHEMA_VERSION = 1
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}\Z")
_SELECTORS = {"slots/A": "A", "slots/B": "B"}
_STABLE = ApplicationState.STABLE.name.lower()
_TRIAL = ApplicationState.TRIAL.name.lower()


@dataclass(frozen=True)
class SlotSnapshot:
    state: SlotState | None
    selected_slot: str | None


def read_snapshot(install_root: Path) -> SlotSnapshot:
    """Read the journal and selector independently; failures become None."""
    root = Path(install_root)
    try:
        state = SlotState.load(root / "state.json")
    except OtaError:
        state = None
    try:
        selected = _SELECTORS.get(os.readlink(root / "active-slot"))
        if selected is not None and not (root / "slots" / selected).is_dir():
            selected = None
    except OSError:
        selected = None
    return SlotSnapshot(state, selected)


def _status(state: str, version: str | None, restored_at: float | None = None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "state": state, "version": version, "restored_at": restored_at}


def decide(snapshot: SlotSnapshot, initial_version: str, previous: dict | None, now: float) -> dict:
    state, selected = snapshot.state, snapshot.selected_slot
    if state is None or selected is None:
        return _status("unknown", None)
    if state.trial_slot is not None and selected == state.trial_slot:
        name, version = _TRIAL, state.trial_version
    elif selected == state.stable_slot:
        name, version = _STABLE, state.stable_version or initial_version
    else:
        return _status("unknown", None)
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        return _status("unknown", None)
    return _status(name, version)
