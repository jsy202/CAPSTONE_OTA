"""Signed recovery orders from the control centre.

An order is the only thing Pi1 accepts as "restore this vehicle". It is signed with a control
key that is separate from the release-signing key, and it is bound to one vehicle, one report
nonce and a short lifetime so it cannot be replayed on another car or later.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .bundle import unique_json, version_key

ORDER_FIELDS = {"incident", "vehicle", "nonce", "action", "release", "version", "expires"}
ACTIONS = {"restore", "reject"}


def sign_order(order: dict, private_key: Path) -> tuple[bytes, bytes]:
    """Return (canonical JSON bytes, Ed25519 signature)."""
    key = serialization.load_pem_private_key(Path(private_key).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("an Ed25519 private key is required")
    raw = json.dumps(order, sort_keys=True).encode()
    return raw, key.sign(raw)


def verify_order(raw: bytes, signature: bytes, public_key: Path, *, vehicle: str, nonce: str, now: float) -> dict:
    """Authenticate the exact bytes first, then check that the order fits this vehicle and moment."""
    key = serialization.load_pem_public_key(Path(public_key).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("an Ed25519 public key is required")
    try:
        key.verify(signature, raw)
    except InvalidSignature as exc:
        raise ValueError("order signature verification failed") from exc
    order = unique_json(raw)
    if not isinstance(order, dict) or set(order) != ORDER_FIELDS:
        raise ValueError("order fields are invalid")
    if order["vehicle"] != vehicle:
        raise ValueError("order is for another vehicle")
    if order["nonce"] != nonce:
        raise ValueError("order nonce does not match this incident")
    if type(order["expires"]) not in (int, float) or order["expires"] <= now:
        raise ValueError("order has expired")
    if order["action"] not in ACTIONS:
        raise ValueError("unknown order action")
    if order["action"] == "restore":
        if not isinstance(order["release"], str) or not order["release"]:
            raise ValueError("restore order needs a release label")
        version_key(order["version"])
    elif order["release"] is not None or order["version"] is not None:
        raise ValueError("reject order must not name a release")
    return order


def default_expiry(lifetime: float = 300.0) -> float:
    return time.time() + lifetime
