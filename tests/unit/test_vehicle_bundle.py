import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from capstone_ota.common.errors import OtaError
from capstone_ota.common.signing import generate_key_pair
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest
from capstone_ota.publisher.vehicle_bundle import build_vehicle_bundle


def bundle_data():
    targets = []
    for ecu_id in ("central-control", "digital-cluster"):
        targets.append({
            "ecu_id": ecu_id, "hardware_id": "rpi-4b", "software_version": "1.2.3",
            "entrypoint": "bin/application", "can_interface": "vehicle-status",
            "protocol_major": 1, "protocol_minor": 2, "capabilities": ["speed", "gear"],
            "release_manifest_url": f"https://updates.local/{ecu_id}.manifest.json",
            "release_signature_url": f"https://updates.local/{ecu_id}.manifest.sig",
            "artifact_url": f"https://updates.local/{ecu_id}.tar.gz",
            "artifact_size": 1234, "artifact_sha256": "a" * 64,
        })
    return {
        "schema_version": 1, "transaction_id": "550e8400-e29b-41d4-a716-446655440000",
        "bundle_version": "2.0.0", "created_at": "2026-10-01T03:00:00Z",
        "expires_at": "2026-10-01T04:00:00Z", "targets": targets,
        "dependencies": [], "rollback_scope": "all",
        "health_policy": {
            "prepare_timeout_s": 60, "activation_timeout_s": 30,
            "verification_timeout_s": 20, "heartbeat_timeout_s": 2,
            "max_missed_heartbeats": 3, "max_error_count": 0,
        },
    }


def parse(data):
    return VehicleBundleManifest.from_bytes(json.dumps(data).encode())


def test_two_target_bundle_is_deeply_immutable_and_canonical():
    data = bundle_data()
    bundle = parse(data)
    reversed_keys = dict(reversed(list(data.items())))
    assert bundle.canonical_bytes() == parse(reversed_keys).canonical_bytes()
    assert bundle.canonical_bytes().startswith(b'{"bundle_version":"2.0.0","created_at":')
    assert b'"protocol_major":1,"protocol_minor":2' in bundle.canonical_bytes()
    assert b" " not in bundle.canonical_bytes()
    assert bundle.transaction_token == 0x550E8400
    assert tuple(target.ecu_id for target in bundle.targets) == ("central-control", "digital-cluster")
    with pytest.raises(FrozenInstanceError):
        bundle.bundle_version = "9.0.0"
    with pytest.raises(FrozenInstanceError):
        bundle.targets[0].software_version = "9.0.0"
    with pytest.raises(TypeError):
        bundle.targets[0].capabilities[0] = "changed"
    data["targets"][0]["capabilities"].append("changed")
    assert bundle.targets[0].capabilities == ("speed", "gear")
    assert parse(json.loads(bundle.canonical_bytes())) == bundle


@pytest.mark.parametrize("roles", [[], ["central-control"], ["digital-cluster"],
    ["central-control", "central-control"], ["central-control", "other"],
    ["central-control", "digital-cluster", "other"]])
def test_duplicate_missing_and_extra_target_roles_are_rejected(roles):
    data = bundle_data()
    template = data["targets"][0]
    data["targets"] = [dict(template, ecu_id=role) for role in roles]
    with pytest.raises(OtaError) as error:
        parse(data)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("schema_version",), 2),
    (("transaction_id",), "not-uuid"), (("bundle_version",), "01.2.3"),
    (("bundle_version",), "1.0.0-01"), (("rollback_scope",), "partial"),
    (("bundle_version",), "1.2.3٣"),
    (("created_at",), "2026-10-01 03:00:00Z"),
    (("created_at",), "2026-10-01T03:00:00+00:00"),
    (("expires_at",), "2026-10-01T03:00:00Z"),
    (("targets", 0, "software_version"), "invalid"),
    (("targets", 0, "artifact_sha256"), "A" * 64),
    (("targets", 0, "artifact_sha256"), "bad"),
    (("targets", 0, "artifact_size"), True), (("targets", 0, "artifact_size"), 0),
    (("targets", 0, "release_manifest_url"), "http://updates.local/app"),
    (("targets", 0, "release_signature_url"), "https://user:pass@updates.local/app"),
    (("targets", 0, "artifact_url"), "https:///app"),
    (("targets", 0, "artifact_url"), "https://updates.local:bad/app"),
    (("targets", 0, "entrypoint"), "bin/../app"),
    (("targets", 0, "entrypoint"), "bin//app"),
    (("targets", 0, "hardware_id"), ""),
    (("targets", 0, "protocol_major"), True),
    (("targets", 0, "protocol_minor"), 256),
    (("targets", 0, "capabilities"), ["speed", "speed"]),
    (("targets", 0, "capabilities"), "speed"),
    (("health_policy", "heartbeat_timeout_s"), 0),
    (("health_policy", "max_error_count"), -1),
])
def test_invalid_fields_are_rejected(path, value):
    data = bundle_data()
    destination = data
    for item in path[:-1]:
        destination = destination[item]
    destination[path[-1]] = value
    with pytest.raises(OtaError) as error:
        parse(data)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


@pytest.mark.parametrize("section", [None, "target", "health_policy"])
@pytest.mark.parametrize("operation", ["extra", "missing"])
def test_exact_nested_schemas_are_enforced(section, operation):
    data = bundle_data()
    destination = data if section is None else (
        data["targets"][0] if section == "target" else data[section])
    if operation == "extra":
        destination["unsigned_extension"] = 1
    else:
        del destination[next(iter(destination))]
    with pytest.raises(OtaError) as error:
        parse(data)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


@pytest.mark.parametrize("raw", [b'\xff', b'[]', b'{', b'null',
    b'{"schema_version":1,"schema_version":1}',
    json.dumps(bundle_data()).replace('"ecu_id": "central-control"',
        '"ecu_id": "central-control", "ecu_id": "central-control"').encode()])
def test_malformed_or_duplicate_json_is_rejected(raw):
    with pytest.raises(OtaError) as error:
        VehicleBundleManifest.from_bytes(raw)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


@pytest.mark.parametrize("hour,minute,code", [(2, 59, "BUNDLE_NOT_YET_VALID"),
    (4, 1, "BUNDLE_EXPIRED")])
def test_bundle_time_policy(hour, minute, code):
    with pytest.raises(OtaError) as error:
        parse(bundle_data()).validate_at(datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc))
    assert error.value.code == code


def test_bundle_time_boundaries_and_naive_time():
    bundle = parse(bundle_data())
    for hour in (3, 4):
        bundle.validate_at(datetime(2026, 10, 1, hour, tzinfo=timezone.utc))
    with pytest.raises(OtaError) as error:
        bundle.validate_at(datetime(2026, 10, 1, 3))
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


def test_token_collision_is_rejected_before_any_artifacts(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(OtaError) as error:
        build_vehicle_bundle(output, parse(bundle_data()), tmp_path / "missing-key.pem", [0x550E8400])
    assert error.value.code == "TRANSACTION_TOKEN_COLLISION"
    assert not output.exists()


def dependency_data():
    return {
        "dependent_ecu_id": "digital-cluster", "provider_ecu_id": "central-control",
        "hardware_id": "rpi-4b", "min_version": "1.2.0", "max_version": "1.9.0",
        "can_interface": "vehicle-status", "protocol_major": 1, "min_protocol_minor": 1,
        "required_capabilities": ["speed"],
    }


def dependent_bundle():
    data = bundle_data()
    data["dependencies"] = [dependency_data()]
    return data


@pytest.mark.parametrize("field,value", [
    ("dependent_ecu_id", "missing-ecu"), ("provider_ecu_id", "missing-ecu"),
    ("hardware_id", "rpi-5"), ("min_version", "1.2.4"), ("max_version", "1.2.2"),
    ("protocol_major", 2), ("min_protocol_minor", 3),
    ("can_interface", "other-interface"), ("required_capabilities", ["brake"]),
])
def test_unsatisfied_dependency_has_stable_error_code(field, value):
    data = dependent_bundle()
    data["dependencies"][0][field] = value
    with pytest.raises(OtaError) as error:
        parse(data).validate_dependencies()
    assert error.value.code == "DEPENDENCY_UNSATISFIED"


@pytest.mark.parametrize("field,value", [("protocol_major", 2), ("can_interface", "other")])
def test_dependent_declaration_must_match_rule(field, value):
    data = dependent_bundle()
    data["targets"][1][field] = value
    with pytest.raises(OtaError) as error:
        parse(data).validate_dependencies()
    assert error.value.code == "DEPENDENCY_UNSATISFIED"


@pytest.mark.parametrize("minimum,maximum,version,accepted", [
    (None, None, "99.0.0", True), ("1.2.3", "1.2.3", "1.2.3+build.42", True),
    ("1.2.3-alpha.2", "1.2.3", "1.2.3-alpha.10", True),
    ("1.2.3", None, "1.2.3-rc.1", False),
    (None, "1.2.3-alpha.2", "1.2.3-alpha.10", False),
])
def test_dependency_bounds_use_semver_precedence(minimum, maximum, version, accepted):
    data = dependent_bundle()
    data["dependencies"][0].update(min_version=minimum, max_version=maximum)
    data["targets"][0]["software_version"] = version
    if accepted:
        parse(data).validate_dependencies()
    else:
        with pytest.raises(OtaError) as error:
            parse(data).validate_dependencies()
        assert error.value.code == "DEPENDENCY_UNSATISFIED"


def test_provider_minor_can_exceed_required_minimum():
    data = dependent_bundle()
    data["targets"][0]["protocol_minor"] = 3
    data["dependencies"][0]["min_protocol_minor"] = 2
    parse(data).validate_dependencies()


@pytest.mark.parametrize("change", [
    {"unexpected": 1}, {"min_version": "bad"}, {"max_version": "1.2.3-01"},
    {"min_version": "9.0.0"}, {"min_protocol_minor": True},
    {"required_capabilities": ["speed", "speed"]},
])
def test_dependency_schema_is_strict(change):
    data = dependent_bundle()
    data["dependencies"][0].update(change)
    with pytest.raises(OtaError) as error:
        parse(data)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


def test_dependency_required_fields_cannot_be_omitted():
    data = dependent_bundle()
    del data["dependencies"][0]["max_version"]
    with pytest.raises(OtaError) as error:
        parse(data)
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"


def test_builder_writes_deterministic_signed_canonical_artifacts(tmp_path):
    from capstone_ota.common.signing import verify_document_signature

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    generate_key_pair(private, public)
    bundle = parse(dependent_bundle())
    first = build_vehicle_bundle(tmp_path / "first", bundle, private)
    second = build_vehicle_bundle(tmp_path / "second", bundle, private)
    assert first.manifest == bundle
    assert first.manifest_path.name == "2.0.0.vehicle-manifest.json"
    assert first.signature_path.name == "2.0.0.vehicle-manifest.sig"
    assert first.manifest_path.read_bytes() == bundle.canonical_bytes()
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first.signature_path.read_bytes() == second.signature_path.read_bytes()
    assert len(first.signature_path.read_bytes()) == 64
    verify_document_signature(bundle, first.signature_path.read_bytes(), public)


def test_builder_rejects_unsatisfied_policy_before_writing(tmp_path):
    data = dependent_bundle()
    data["dependencies"][0]["required_capabilities"] = ["brake"]
    with pytest.raises(OtaError) as error:
        build_vehicle_bundle(tmp_path / "output", parse(data), tmp_path / "missing-key.pem")
    assert error.value.code == "DEPENDENCY_UNSATISFIED"
    assert not (tmp_path / "output").exists()


def test_distinct_uuids_with_equal_prefix_collide(tmp_path):
    first = parse(bundle_data())
    data = bundle_data()
    data["transaction_id"] = "550e8400-0000-4000-8000-000000000001"
    second = parse(data)
    assert second.transaction_id != first.transaction_id
    with pytest.raises(OtaError) as error:
        build_vehicle_bundle(tmp_path / "output", second, tmp_path / "missing.pem",
                             [first.transaction_token])
    assert error.value.code == "TRANSACTION_TOKEN_COLLISION"
    assert not (tmp_path / "output").exists()


def test_builder_revalidates_manually_constructed_bundle(tmp_path):
    from dataclasses import replace

    bundle = replace(parse(bundle_data()), rollback_scope="partial")
    with pytest.raises(OtaError) as error:
        build_vehicle_bundle(tmp_path / "output", bundle, tmp_path / "missing.pem")
    assert error.value.code == "INVALID_VEHICLE_BUNDLE"
    assert not (tmp_path / "output").exists()


def test_invalid_signing_key_does_not_create_artifacts(tmp_path):
    with pytest.raises(OtaError) as error:
        build_vehicle_bundle(tmp_path / "output", parse(bundle_data()), tmp_path / "missing.pem")
    assert error.value.code == "SIGNING_KEY_INVALID"
    assert not (tmp_path / "output").exists()


def test_changed_bundle_cannot_use_original_signature(tmp_path):
    from capstone_ota.common.signing import sign_document, verify_document_signature

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    generate_key_pair(private, public)
    signature = sign_document(parse(bundle_data()), private)
    changed = bundle_data()
    changed["targets"][0]["artifact_sha256"] = "b" * 64
    with pytest.raises(OtaError) as error:
        verify_document_signature(parse(changed), signature, public)
    assert error.value.code == "SIGNATURE_INVALID"
