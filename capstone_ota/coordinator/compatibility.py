"""Pure compatibility checks over independently observed application values.

v1 uses speed in tenths of km/h (0..3000), RPM 0..12000, four gears,
and an eight-bit warning mask. Default heartbeat period is 1 s and vehicle
status period is 100 ms; deployment may inject its explicitly agreed contract.
At least two fresh samples prove rolling counters. No network I/O occurs here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from capstone_ota.common.can_protocol import (
    ApplicationState, CanFrame, FunctionalTestRequestFrame, FunctionalTestResultFrame,
    HeartbeatFrame, VehicleStatusFrame, is_counter_contiguous,
)
from capstone_ota.common.errors import OtaError
from capstone_ota.common.vehicle_bundle import VehicleBundleManifest


@dataclass(frozen=True)
class FrameObservation:
    """Raw receiver observation; malformed DLC/CRC must remain observable."""
    observed_at: float
    can_id: int
    data: bytes


@dataclass(frozen=True)
class CompatibilitySnapshot:
    """One bounded window collected after activation/recovery.

    process_health means continuously healthy throughout this window, not a
    single pre-update PID check. Functional responses come from Cluster IPC,
    never from echoing challenge inputs. Times share the injected monotonic clock.
    """
    started_at: float
    ended_at: float
    process_health: dict[str, bool]
    frames: tuple[FrameObservation, ...]
    requests: tuple[FunctionalTestRequestFrame, ...]
    functional_results: tuple[FrameObservation, ...]


@dataclass(frozen=True)
class CompatibilityResult:
    passed: bool
    errors: tuple[dict, ...]
    evidence: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": list(self.errors), "evidence": list(self.evidence)}


@dataclass(frozen=True)
class CanContract:
    heartbeat_period_s: float = 1.0
    vehicle_period_s: float = 0.1
    max_speed: int = 3000
    max_rpm: int = 12000
    allowed_warning_mask: int = 255

    def __post_init__(self):
        for value in (self.heartbeat_period_s, self.vehicle_period_s):
            if not _time(value) or value <= 0:
                raise ValueError("CAN periods must be positive finite numbers")
        for value, maximum in ((self.max_speed, 65535), (self.max_rpm, 65535),
                               (self.allowed_warning_mask, 255)):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("CAN signal limits must be wire-representable")


def _time(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


class CompatibilityValidator:
    def __init__(self, contract: CanContract | None = None):
        self.contract = contract or CanContract()

    def validate_static(self, bundle: VehicleBundleManifest) -> CompatibilityResult:
        try:
            bundle.validate_dependencies()
        except OtaError as exc:
            return CompatibilityResult(False, ({"code": exc.code, "message": exc.message},),
                                       ({"check": "dependencies", "passed": False},))
        if len({(target.can_interface, target.protocol_major) for target in bundle.targets}) != 1:
            return CompatibilityResult(False, ({"code": "CAN_DECLARATION_MISMATCH",
                "message": "both applications require a shared CAN interface and major"},), ())
        # Numeric CAN heartbeat versions cannot express prerelease/build aliases.
        for target in bundle.targets:
            parts = target.software_version.split(".")
            if len(parts) != 3 or any(not p.isdecimal() or int(p) > 255 for p in parts):
                return CompatibilityResult(False, ({"code": "VERSION_NOT_WIRE_REPRESENTABLE",
                    "ecu_id": target.ecu_id, "message": "heartbeat requires uint8 numeric SemVer triple"},), ())
        return CompatibilityResult(True, (), ({"check": "dependencies", "passed": True},))

    def validate_runtime(self, bundle: VehicleBundleManifest, snapshot: CompatibilitySnapshot,
                         *, trial: bool, not_before: float) -> CompatibilityResult:
        errors, evidence, fatal, can_errors = [], [], [], []

        def fail(code, message, ecu_id=None, *, can=False, **details):
            item = {"code": code}
            if ecu_id is not None:
                item["ecu_id"] = ecu_id
            item.update(message=message, **details)
            errors.append(item)
            (can_errors if can else fatal).append(item)

        if (not isinstance(snapshot, CompatibilitySnapshot)
                or not _time(snapshot.started_at) or not _time(snapshot.ended_at)
                or not _time(not_before) or snapshot.started_at < not_before
                or snapshot.ended_at <= snapshot.started_at
                or snapshot.ended_at - snapshot.started_at < self.contract.heartbeat_period_s
                or snapshot.ended_at - snapshot.started_at > bundle.health_policy.verification_timeout_s):
            fail("OBSERVATION_WINDOW_INVALID", "window must be fresh, positive and within verification deadline")
            return CompatibilityResult(False, tuple(errors), ())
        policy, contract = bundle.health_policy, self.contract
        targets = {target.ecu_id: target for target in bundle.targets}
        for role in targets:
            healthy = isinstance(snapshot.process_health, dict) and snapshot.process_health.get(role) is True
            evidence.append({"check": "process", "ecu_id": role, "passed": healthy})
            if not healthy:
                fail("PROCESS_UNHEALTHY", "application did not stay healthy throughout window", role)
        heartbeats = {role: [] for role in targets}
        vehicle = []

        def decode(observation, codec):
            if (not isinstance(observation, FrameObservation) or not _time(observation.observed_at)
                    or not snapshot.started_at <= observation.observed_at <= snapshot.ended_at):
                fail("OBSERVATION_WINDOW_INVALID", "sample is outside the fresh verification window")
                return None
            try:
                return codec.decode(CanFrame(observation.can_id, observation.data))
            except (ValueError, TypeError) as exc:
                fail("CAN_FRAME_INVALID", str(exc), can=True)
                return None

        for observation in snapshot.frames:
            if not isinstance(observation, FrameObservation):
                fail("OBSERVATION_INVALID", "receiver must provide raw FrameObservation values")
                continue
            if observation.can_id in {0x100, 0x101}:
                decoded = decode(observation, HeartbeatFrame)
                if decoded is not None:
                    heartbeats[decoded.ecu_id].append((observation.observed_at, decoded))
            elif observation.can_id == 0x200:
                decoded = decode(observation, VehicleStatusFrame)
                if decoded is not None:
                    vehicle.append((observation.observed_at, decoded))
            else:
                fail("CAN_ID_UNEXPECTED", "unexpected ID in application contract observations",
                     can=True, can_id=observation.can_id)
        expected_state = ApplicationState.TRIAL if trial else ApplicationState.STABLE

        def sequence(samples, period, role, prefix):
            if len(samples) < 2:
                fail(f"{prefix}_MISSING" if prefix == "HEARTBEAT" else "CAN_OBSERVATION_MISSING",
                     "at least two fresh samples are required", role)
                return
            previous = None
            previous_time = snapshot.started_at
            missed = 0
            for timestamp, value in samples:
                if timestamp < previous_time:
                    fail("OBSERVATION_WINDOW_INVALID", "receiver timestamps moved backwards", role)
                if not is_counter_contiguous(value.counter, previous):
                    fail(f"{prefix}_COUNTER_STALE", "rolling counter is not contiguous", role,
                         can=prefix == "CAN")
                missed += max(0, int((timestamp - previous_time + 1e-9) / period) - 1)
                previous, previous_time = value.counter, timestamp
            missed += max(0, int((snapshot.ended_at - previous_time + 1e-9) / period))
            if missed > policy.max_missed_heartbeats:
                fail(f"{prefix}_PERIOD_MISSED", "missed-period budget exceeded", role, missed=missed)
            timeout = policy.heartbeat_timeout_s if prefix == "HEARTBEAT" else period * (policy.max_missed_heartbeats + 1)
            if snapshot.ended_at - previous_time > timeout:
                fail(f"{prefix}_TIMEOUT", "last observation is too old", role)

        for role, samples in heartbeats.items():
            target = targets[role]
            try:
                expected_version = tuple(int(p) for p in target.software_version.split("."))
            except ValueError:
                expected_version = ()
            sequence(samples, contract.heartbeat_period_s, role, "HEARTBEAT")
            for _, heartbeat in samples:
                if (heartbeat.software_version != expected_version
                        or (heartbeat.protocol_major, heartbeat.protocol_minor) != (target.protocol_major, target.protocol_minor)):
                    fail("HEARTBEAT_VERSION_MISMATCH", "software/protocol differs from expected version set", role,
                         expected=target.software_version, observed=list(heartbeat.software_version))
                if heartbeat.state != expected_state:
                    fail("HEARTBEAT_STATE_MISMATCH", "application state differs from verification phase", role)
            evidence.append({"check": "heartbeat", "ecu_id": role, "samples": len(samples),
                             "expected_version": target.software_version, "expected_state": int(expected_state)})
        sequence(vehicle, contract.vehicle_period_s, "central-control", "CAN")
        for _, value in vehicle:
            if (value.speed > contract.max_speed or value.rpm > contract.max_rpm
                    or value.warnings & ~contract.allowed_warning_mask):
                fail("CAN_VALUE_OUT_OF_RANGE", "vehicle signals violate physical contract", can=True)
        evidence.append({"check": "can", "samples": len(vehicle), "error_count": len(can_errors),
                         "max_error_count": policy.max_error_count})
        requests = {}
        for request in snapshot.requests:
            try:
                request.encode()
                if request.test_id in requests:
                    raise ValueError("duplicate functional test ID")
                requests[request.test_id] = request
            except (ValueError, AttributeError, TypeError) as exc:
                fail("FUNCTIONAL_REQUEST_INVALID", str(exc))
        if not requests:
            fail("FUNCTIONAL_TEST_MISSING", "a functional challenge is required")
        seen = set()
        for observation in snapshot.functional_results:
            result = decode(observation, FunctionalTestResultFrame)
            if result is None:
                fail("FUNCTIONAL_RESULT_INVALID", "functional result cannot be decoded")
                continue
            request = requests.get(result.test_id)
            if request is None or result.test_id in seen:
                fail("FUNCTIONAL_RESULT_UNEXPECTED", "uncorrelated or duplicate functional result", test_id=result.test_id)
                continue
            seen.add(result.test_id)
            expected = [request.speed, request.rpm, int(request.gear), request.warnings]
            observed = [result.speed, result.rpm, int(result.gear), result.warnings]
            if expected != observed:
                fail("FUNCTIONAL_VALUE_MISMATCH", "interpreted values differ", "digital-cluster",
                     test_id=result.test_id, expected=expected, observed=observed)
            evidence.append({"check": "functional", "test_id": result.test_id,
                             "expected": expected, "observed": observed, "passed": expected == observed})
        for test_id in requests.keys() - seen:
            fail("FUNCTIONAL_RESULT_MISSING", "no interpreted result for challenge", test_id=test_id)
        return CompatibilityResult(not fatal and len(can_errors) <= policy.max_error_count,
                                   tuple(errors), tuple(evidence))
