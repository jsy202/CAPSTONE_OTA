"""Signed recovery orders: what Pi1 will accept as 'the control centre said restore'."""
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from capstone_ota.order import sign_order, verify_order

NONCE = "ab" * 16


def make_keypair(tmp_path, name):
    private = Ed25519PrivateKey.generate()
    key, public = tmp_path / f"{name}.key", tmp_path / f"{name}.pub"
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, public


@pytest.fixture
def order_keys(tmp_path):
    return make_keypair(tmp_path, "order")


def make_order(**changes):
    order = {"incident": "INC-0001", "vehicle": "PI2-001", "nonce": NONCE, "action": "restore",
             "release": "v2", "version": "2.0.0", "expires": time.time() + 300}
    order.update(changes)
    return order


def check(raw, signature, public, **overrides):
    arguments = dict(vehicle="PI2-001", nonce=NONCE, now=time.time())
    arguments.update(overrides)
    return verify_order(raw, signature, public, **arguments)


def test_round_trip(order_keys):
    raw, signature = sign_order(make_order(), order_keys[0])
    assert check(raw, signature, order_keys[1])["version"] == "2.0.0"


def test_reject_order_carries_no_release(order_keys):
    raw, signature = sign_order(make_order(action="reject", release=None, version=None), order_keys[0])
    assert check(raw, signature, order_keys[1])["action"] == "reject"


def test_rejects_wrong_signing_key(tmp_path, order_keys):
    raw, signature = sign_order(make_order(), make_keypair(tmp_path, "attacker")[0])
    with pytest.raises(ValueError, match="signature"):
        check(raw, signature, order_keys[1])


@pytest.mark.parametrize("override,message", [
    ({"vehicle": "OTHER-CAR"}, "vehicle"),
    ({"nonce": "cd" * 16}, "nonce"),
    ({"now": time.time() + 10_000}, "expired"),
])
def test_rejects_mismatched_context(order_keys, override, message):
    raw, signature = sign_order(make_order(), order_keys[0])
    with pytest.raises(ValueError, match=message):
        check(raw, signature, order_keys[1], **override)


def test_rejects_tampered_bytes(order_keys):
    raw, signature = sign_order(make_order(), order_keys[0])
    forged = raw.replace(b"2.0.0", b"1.0.0")
    with pytest.raises(ValueError, match="signature"):
        check(forged, signature, order_keys[1])


@pytest.mark.parametrize("changes", [
    {"action": "wipe"},
    {"action": "restore", "release": None, "version": None},
    {"action": "reject", "release": "v2", "version": "2.0.0"},
    {"version": "two"},
])
def test_rejects_malformed_but_signed_orders(order_keys, changes):
    raw, signature = sign_order(make_order(**changes), order_keys[0])
    with pytest.raises(ValueError):
        check(raw, signature, order_keys[1])


def test_rejects_unexpected_keys(order_keys):
    order = make_order()
    order["extra"] = 1
    raw, signature = sign_order(order, order_keys[0])
    with pytest.raises(ValueError, match="fields"):
        check(raw, signature, order_keys[1])
