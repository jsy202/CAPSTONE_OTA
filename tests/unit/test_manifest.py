import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest


def manifest_data(**overrides):
    data = {
        "schema_version": 1,
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "device_id": "cluster-pi-01",
        "version": "1.2.3",
        "created_at": "2026-09-29T03:00:00Z",
        "expires_at": "2026-09-29T04:00:00Z",
        "artifact_url": "https://laptop.local:8443/releases/1.2.3.tar.gz",
        "artifact_size": 1234,
        "artifact_sha256": "a" * 64,
        "entrypoint": "bin/digital-dash",
    }
    data.update(overrides)
    return data


def encode(data):
    return json.dumps(data).encode("utf-8")


def assert_invalid(data):
    with pytest.raises(OtaError) as error:
        ReleaseManifest.from_bytes(encode(data))
    assert error.value.code == "INVALID_MANIFEST"


def test_manifest_is_immutable_and_canonical_bytes_ignore_input_key_order():
    forward = manifest_data()
    reverse = dict(reversed(list(forward.items())))

    first = ReleaseManifest.from_bytes(encode(forward))
    second = ReleaseManifest.from_bytes(encode(reverse))

    assert first == second
    assert first.canonical_bytes() == (
        b'{"artifact_sha256":"' + b"a" * 64
        + b'","artifact_size":1234,"artifact_url":"https://laptop.local:8443/releases/1.2.3.tar.gz",'
        b'"created_at":"2026-09-29T03:00:00Z","device_id":"cluster-pi-01",'
        b'"entrypoint":"bin/digital-dash","expires_at":"2026-09-29T04:00:00Z",'
        b'"job_id":"550e8400-e29b-41d4-a716-446655440000","schema_version":1,"version":"1.2.3"}'
    )
    with pytest.raises(FrozenInstanceError):
        first.version = "2.0.0"


def test_duplicate_json_key_is_rejected():
    raw = encode(manifest_data()).decode("utf-8")
    raw = raw[:-1] + ', "version": "9.9.9"}'

    with pytest.raises(OtaError) as error:
        ReleaseManifest.from_bytes(raw.encode("utf-8"))

    assert error.value.code == "INVALID_MANIFEST"


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": 2},
        {"artifact_sha256": "A" * 64},
        {"artifact_sha256": "bad"},
        {"artifact_url": "http://laptop/releases/app.tar.gz"},
        {"artifact_size": 0},
        {"entrypoint": "/bin/digital-dash"},
        {"entrypoint": "../digital-dash"},
        {"entrypoint": "bin/../digital-dash"},
        {"created_at": "2026-09-29 03:00:00"},
        {"expires_at": "not-a-time"},
        {"version": "version-one"},
    ],
)
def test_invalid_manifest_fields_are_rejected(overrides):
    assert_invalid(manifest_data(**overrides))


def test_unknown_or_missing_fields_are_rejected():
    unknown = manifest_data(extra="not-signed-contract")
    missing = manifest_data()
    del missing["job_id"]

    assert_invalid(unknown)
    assert_invalid(missing)


def test_policy_accepts_newer_release_for_matching_device():
    manifest = ReleaseManifest.from_bytes(encode(manifest_data()))
    manifest.validate_for(
        device_id="cluster-pi-01",
        current_version="1.2.2",
        now=datetime(2026, 9, 29, 3, 30, tzinfo=timezone.utc),
    )


def test_policy_accepts_first_install_without_current_version():
    manifest = ReleaseManifest.from_bytes(encode(manifest_data()))
    manifest.validate_for(
        device_id="cluster-pi-01",
        current_version=None,
        now=datetime(2026, 9, 29, 3, 30, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    ("device_id", "current_version", "now", "expected_code"),
    [
        ("another-pi", "1.2.2", datetime(2026, 9, 29, 3, 30, tzinfo=timezone.utc), "WRONG_DEVICE"),
        ("cluster-pi-01", "1.2.2", datetime(2026, 9, 29, 4, 0, 1, tzinfo=timezone.utc), "EXPIRED"),
        ("cluster-pi-01", "1.2.3", datetime(2026, 9, 29, 3, 30, tzinfo=timezone.utc), "ROLLBACK_REJECTED"),
        ("cluster-pi-01", "2.0.0", datetime(2026, 9, 29, 3, 30, tzinfo=timezone.utc), "ROLLBACK_REJECTED"),
    ],
)
def test_policy_rejects_wrong_target_expired_or_old_release(
    device_id, current_version, now, expected_code
):
    manifest = ReleaseManifest.from_bytes(encode(manifest_data()))

    with pytest.raises(OtaError) as error:
        manifest.validate_for(device_id=device_id, current_version=current_version, now=now)

    assert error.value.code == expected_code


def test_policy_requires_timezone_aware_now():
    manifest = ReleaseManifest.from_bytes(encode(manifest_data()))
    with pytest.raises(OtaError) as error:
        manifest.validate_for("cluster-pi-01", None, datetime.now())
    assert error.value.code == "INVALID_MANIFEST"
