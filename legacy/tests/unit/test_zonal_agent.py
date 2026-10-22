import importlib
import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from capstone_ota.agent.slots import ABSlotInstaller, SlotState
from capstone_ota.common.can_protocol import (
    ApplicationState, CanFrame, FunctionalTestRequestFrame, FunctionalTestResultFrame,
    Gear, HeartbeatFrame, OtaCommand, OtaCommandFrame, OtaStatus, Slot, VehicleStatusFrame,
)
from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import generate_key_pair, sign_manifest
from capstone_ota.publisher.bundle import BundleRequest, build_release
from tests.unit.test_zonal_config import configuration, load


TX = "550e8400-e29b-41d4-a716-446655440000"
TOKEN = 0x550E8400
JOB = "660e8400-e29b-41d4-a716-446655440000"


class Services:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.restarts = 0

    def restart_and_wait_healthy(self, unit, timeout):
        self.restarts += 1
        return self.healthy


class Transport:
    def __init__(self):
        self.sent = []
        self.incoming = []

    def send(self, frame):
        self.sent.append(frame)

    def recv(self, timeout):
        return self.incoming.pop(0) if self.incoming else None


def setup_agent(tmp_path):
    module = importlib.import_module("capstone_ota.agent.zonal")
    config = load(tmp_path, configuration(tmp_path))
    private = tmp_path / "private.pem"
    config.public_key.unlink()
    generate_key_pair(private, config.public_key)
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    source = tmp_path / "source"
    (source / "bin").mkdir(parents=True)
    (source / "bin" / "dash").write_text("#!/bin/sh\necho trial\n")
    (source / "bin" / "dash").chmod(0o755)
    release = build_release(source, tmp_path / "release", BundleRequest(
        device_id=config.device_id, version="2.3.4", entrypoint="bin/dash",
        artifact_base_url="https://publisher.local/releases", private_key_path=private,
        job_id=JOB, created_at=now - timedelta(minutes=1), expires_at=now + timedelta(hours=1),
    ))
    metadata = {
        "transaction.json": json.dumps({"schema_version": 1, "transaction_id": TX, "release_job_id": JOB}).encode(),
        "manifest.json": release.manifest_path.read_bytes(),
        "manifest.sig": release.signature_path.read_bytes(),
    }
    calls = []
    root = "https://10.10.0.1:8443/transactions/550e8400/cluster/"

    def fetch(url, ca, maximum):
        assert url.startswith(root)
        assert ca == config.ca_file
        calls.append(url)
        return metadata[url.removeprefix(root)]

    def download(url, destination, ca, expected, maximum):
        assert url == root + "artifact.tar.gz"
        assert ca == config.ca_file
        calls.append(url)
        shutil.copyfile(release.archive_path, destination)

    services = Services()
    installer = ABSlotInstaller(config.install_root, services, config.service_unit)
    transport = Transport()
    clock = [0.0]
    agent = module.ZonalAgent(config, installer, transport, fetch_bytes=fetch,
                             artifact_downloader=download, now=lambda: now,
                             monotonic=lambda: clock[0])
    return agent, installer, transport, metadata, calls, services, clock


def command(kind, counter=0, token=TOKEN, slot=Slot.B):
    return OtaCommandFrame(kind, token, slot, counter=counter).encode()


def test_prepare_verifies_release_stages_inactive_slot_and_persists_uuid(tmp_path):
    agent, installer, _, _, calls, _, _ = setup_agent(tmp_path)
    result = agent.handle_command(command(OtaCommand.PREPARE))
    assert result.status == OtaStatus.READY
    assert result.transaction_token == TOKEN and result.slot == Slot.B
    assert installer.state.phase == "staged"
    assert installer.state.transaction_id == TX
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    assert json.loads(agent.config.state_file.read_text())["token_history"] == {"550e8400": TX}
    assert len(calls) == 4


def test_activate_commit_and_repeated_commands_preserve_terminal_outcome(tmp_path):
    agent, installer, _, _, calls, services, _ = setup_agent(tmp_path)
    assert agent.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.READY
    assert agent.handle_command(command(OtaCommand.PREPARE, 1)).status == OtaStatus.READY
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 2)).status == OtaStatus.VERIFYING
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 3)).status == OtaStatus.VERIFYING
    assert agent.handle_command(command(OtaCommand.COMMIT, 4)).status == OtaStatus.COMMITTED
    assert agent.handle_command(command(OtaCommand.COMMIT, 5)).status == OtaStatus.COMMITTED
    assert agent.handle_command(command(OtaCommand.QUERY_STATUS, 6)).status == OtaStatus.COMMITTED
    assert installer.state.stable_slot == "B"
    assert services.restarts == 1
    assert sum(url.endswith("artifact.tar.gz") for url in calls) == 1


def test_trial_rollback_restores_stable_and_blocks_late_commit(tmp_path):
    agent, installer, _, _, _, services, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    agent.handle_command(command(OtaCommand.ACTIVATE, 1))
    assert agent.handle_command(command(OtaCommand.ROLLBACK, 2)).status == OtaStatus.ROLLED_BACK
    assert agent.handle_command(command(OtaCommand.ROLLBACK, 3)).status == OtaStatus.ROLLED_BACK
    assert agent.handle_command(command(OtaCommand.COMMIT, 4)).status == OtaStatus.ERROR
    assert installer.state.stable_slot == "A" and services.restarts == 2


@pytest.mark.parametrize("mutation", ["signature", "archive", "device", "job", "expired", "version", "metadata"])
def test_prepare_rejects_untrusted_or_invalid_release_before_staging(tmp_path, mutation):
    agent, installer, _, metadata, _, _, _ = setup_agent(tmp_path)
    if mutation == "signature":
        metadata["manifest.sig"] = bytes(64)
    elif mutation == "archive":
        original = agent.artifact_downloader
        def corrupt(*args):
            original(*args)
            args[1].write_bytes(args[1].read_bytes() + b"tampered")
        agent.artifact_downloader = corrupt
    elif mutation == "metadata":
        metadata["transaction.json"] = b'{"schema_version":true}'
    else:
        manifest = json.loads(metadata["manifest.json"])
        field, value = {"device": ("device_id", "other"), "job": ("job_id", TX),
                        "expired": ("expires_at", "2026-10-01T00:00:00Z"),
                        "version": ("version", "256.0.0")}[mutation]
        manifest[field] = value
        metadata["manifest.json"] = json.dumps(manifest).encode()
    assert agent.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert installer.state.phase == "stable"


@pytest.mark.parametrize("field,value,code", [
    ("device_id", "other", "WRONG_DEVICE"),
    ("job_id", TX, "TRANSACTION_INVALID"),
    ("expires_at", "2026-10-01T23:59:59Z", "EXPIRED"),
    ("version", "256.0.0", "VERSION_UNREPRESENTABLE"),
])
def test_authentically_signed_but_inapplicable_release_is_rejected(tmp_path, field, value, code):
    agent, installer, _, metadata, _, _, _ = setup_agent(tmp_path)
    raw = json.loads(metadata["manifest.json"])
    raw[field] = value
    manifest = ReleaseManifest.from_bytes(json.dumps(raw).encode())
    metadata["manifest.json"] = manifest.canonical_bytes()
    metadata["manifest.sig"] = sign_manifest(manifest, tmp_path / "private.pem")
    assert agent.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert agent.last_error.code == code
    assert installer.state.phase == "stable"


def test_wrong_token_wrong_slot_and_out_of_order_command_cannot_mutate(tmp_path):
    agent, installer, _, _, _, _, _ = setup_agent(tmp_path)
    assert agent.handle_command(command(OtaCommand.COMMIT)).status == OtaStatus.ERROR
    assert agent.handle_command(command(OtaCommand.PREPARE, 1)).status == OtaStatus.READY
    wrong_token = agent.handle_command(command(OtaCommand.ACTIVATE, 2, token=1))
    assert wrong_token.status == OtaStatus.ERROR and wrong_token.detail == 3
    wrong_slot = agent.handle_command(command(OtaCommand.ACTIVATE, 3, slot=Slot.A))
    assert wrong_slot.status == OtaStatus.ERROR and wrong_slot.detail == 4
    assert installer.state.phase == "staged"


def test_duplicate_frame_is_idempotent_but_stale_and_skipped_counter_reject(tmp_path):
    agent, installer, _, _, _, _, clock = setup_agent(tmp_path)
    frame = command(OtaCommand.PREPARE, 14)
    assert agent.handle_command(frame).status == OtaStatus.READY
    assert agent.handle_command(frame).status == OtaStatus.READY
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 14)).status == OtaStatus.ERROR
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 0)).status == OtaStatus.ERROR
    clock[0] = 31
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 15)).status == OtaStatus.ERROR
    assert installer.state.phase == "staged"


def test_corrupt_command_raises_and_poll_drops_invalid_frames(tmp_path):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    frame = command(OtaCommand.PREPARE)
    broken = CanFrame(frame.can_id, frame.data[:-1] + bytes([frame.data[-1] ^ 1]))
    with pytest.raises(ValueError, match="CRC"):
        agent.handle_command(broken)
    transport.incoming = [broken, frame]
    assert agent.poll_once(0) is None
    assert agent.poll_once(0).status == OtaStatus.READY
    assert transport.sent[-1].can_id == 0x601
    assert installer.state.phase == "staged"


def test_token_collision_rejected_against_retained_history_after_restart(tmp_path):
    agent, installer, transport, metadata, _, _, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    agent.handle_command(command(OtaCommand.ROLLBACK, 1))
    data = json.loads(metadata["transaction.json"])
    data["transaction_id"] = "550e8400-0000-4000-8000-000000000001"
    metadata["transaction.json"] = json.dumps(data).encode()
    restarted = type(agent)(agent.config, installer, transport, fetch_bytes=agent.fetch_bytes,
                            artifact_downloader=agent.artifact_downloader, now=agent.now)
    assert restarted.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert installer.state.phase == "stable"
    assert installer.state.completed_transactions == {TX: "rolled_back"}


def test_startup_recovery_rolls_back_trial_and_retains_uuid_mapping(tmp_path):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    agent.handle_command(command(OtaCommand.ACTIVATE, 1))
    restarted = type(agent)(agent.config, installer, transport)
    assert installer.state.stable_slot == "A" and installer.state.phase == "stable"
    assert restarted.handle_command(command(OtaCommand.QUERY_STATUS)).status == OtaStatus.ROLLED_BACK


def test_corrupt_history_blocks_startup_without_mutating_slots(tmp_path):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    agent.config.state_file.write_text('{"schema_version":1}')
    with pytest.raises(OtaError):
        type(agent)(agent.config, installer, transport)
    assert installer.state.phase == "stable"


def test_heartbeat_tracks_trial_and_commit_numeric_version_and_rollover(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    first = agent.publish_heartbeat()
    assert first.software_version == (0, 0, 0)
    assert first.state == ApplicationState.STABLE
    assert first.ecu_id == "digital-cluster"
    assert (first.protocol_major, first.protocol_minor) == (1, 0)
    assert HeartbeatFrame.decode(transport.sent[-1]) == first
    agent.handle_command(command(OtaCommand.PREPARE))
    assert agent.publish_heartbeat().software_version == (0, 0, 0)
    agent.handle_command(command(OtaCommand.ACTIVATE, 1))
    trial = agent.publish_heartbeat()
    assert trial.software_version == (2, 3, 4) and trial.state == ApplicationState.TRIAL
    agent.handle_command(command(OtaCommand.COMMIT, 2))
    for _ in range(253):
        last = agent.publish_heartbeat()
    assert last.counter == 255 and last.state == ApplicationState.STABLE
    assert agent.publish_heartbeat().counter == 0


def test_functional_result_uses_application_observation_and_request_test_id(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    module = importlib.import_module("capstone_ota.agent.zonal")
    class Application:
        def apply_vehicle_status(self, sample):
            self.rpm = sample.rpm + 17

        def observe_functional_test(self, request):
            return module.ApplicationObservation(321, self.rpm, Gear.NEUTRAL, 0x12)

    agent.application_adapter = Application()
    transport.incoming = [VehicleStatusFrame(450, 1000, Gear.DRIVE, 0, 7).encode(),
                          FunctionalTestRequestFrame(450, 1000, Gear.DRIVE, 0, 83).encode()]
    assert agent.poll_once(0) is None
    result = agent.poll_once(0)
    assert (result.speed, result.rpm, result.gear, result.warnings, result.test_id) == (321, 1017, Gear.NEUTRAL, 0x12, 83)
    assert FunctionalTestResultFrame.decode(transport.sent[-1]) == result


def test_missing_application_adapter_never_fabricates_functional_success(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    transport.incoming = [FunctionalTestRequestFrame(450, 1000, Gear.DRIVE, 0, 1).encode()]
    assert agent.poll_once(0) is None
    assert transport.sent == []
    assert agent.last_error.code == "APPLICATION_ADAPTER_UNAVAILABLE"


def test_vehicle_counter_loss_cannot_feed_application_adapter(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    class Application:
        def apply_vehicle_status(self, sample):
            self.displayed_rpm = sample.rpm
    app = Application()
    agent.application_adapter = app
    transport.incoming = [VehicleStatusFrame(1, 100, Gear.DRIVE, 0, 255).encode(),
                          VehicleStatusFrame(1, 200, Gear.DRIVE, 0, 0).encode(),
                          VehicleStatusFrame(1, 300, Gear.DRIVE, 0, 2).encode()]
    agent.poll_once(0)
    agent.poll_once(0)
    agent.poll_once(0)
    assert app.displayed_rpm == 200


def test_query_status_resynchronizes_after_command_timeout_without_activation(tmp_path):
    agent, installer, _, _, _, _, clock = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    clock[0] = 31
    assert agent.handle_command(command(OtaCommand.QUERY_STATUS, 8)).status == OtaStatus.READY
    assert installer.state.phase == "staged"
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 9)).status == OtaStatus.VERIFYING


def test_initial_application_version_protects_provisioned_slot_from_downgrade(tmp_path):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    restarted = type(agent)(agent.config, installer, transport, fetch_bytes=agent.fetch_bytes,
                            artifact_downloader=agent.artifact_downloader, now=agent.now,
                            initial_software_version=(3, 0, 0))
    assert restarted.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert restarted.last_error.code == "ROLLBACK_REJECTED"
    assert installer.state.phase == "stable"


def test_constructor_rejects_installer_for_other_install_root(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    other = ABSlotInstaller(tmp_path / "unrelated", Services(), agent.config.service_unit)
    with pytest.raises(OtaError, match="ZONAL_CONFIG_MISMATCH"):
        type(agent)(agent.config, other, transport)


def test_functional_adapter_io_failure_is_recorded_without_publishing(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    class OfflineApplication:
        def observe_functional_test(self, request):
            raise OSError("application IPC disconnected")
    agent.application_adapter = OfflineApplication()
    transport.incoming = [FunctionalTestRequestFrame(450, 1000, Gear.DRIVE, 0, 1).encode()]
    assert agent.poll_once(0) is None
    assert transport.sent == []
    assert agent.last_error.code == "APPLICATION_OBSERVATION_FAILED"


def test_runtime_slot_journal_corruption_reports_recovery_failed(tmp_path):
    agent, installer, _, _, _, _, _ = setup_agent(tmp_path)
    installer.state_path.write_text("corrupt")
    result = agent.handle_command(command(OtaCommand.QUERY_STATUS, token=0))
    assert result.status == OtaStatus.RECOVERY_FAILED and result.detail == 6
    assert installer.active_link.resolve() == installer.slots_dir / "A"


def test_history_write_failure_cannot_acknowledge_ready_or_stage(tmp_path, monkeypatch):
    agent, installer, _, _, _, _, _ = setup_agent(tmp_path)
    def interrupted_write():
        raise OSError("journal fsync failed")
    monkeypatch.setattr(agent, "_save_history", interrupted_write)
    assert agent.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert installer.state.phase == "stable"


def test_archive_hash_mismatch_of_equal_length_cannot_stage(tmp_path):
    agent, installer, _, _, _, _, _ = setup_agent(tmp_path)
    original = agent.artifact_downloader
    def corrupt(*args):
        original(*args)
        content = args[1].read_bytes()
        args[1].write_bytes(bytes([content[0] ^ 1]) + content[1:])
    agent.artifact_downloader = corrupt
    assert agent.handle_command(command(OtaCommand.PREPARE)).status == OtaStatus.ERROR
    assert agent.last_error.code == "ARTIFACT_HASH_MISMATCH"
    assert installer.state.phase == "stable"


def test_active_token_collision_is_rejected_without_changing_staged_uuid(tmp_path):
    agent, installer, _, metadata, _, _, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    data = json.loads(metadata["transaction.json"])
    data["transaction_id"] = "550e8400-0000-4000-8000-000000000001"
    metadata["transaction.json"] = json.dumps(data).encode()
    assert agent.handle_command(command(OtaCommand.PREPARE, 1)).status == OtaStatus.ERROR
    assert agent.last_error.code == "TRANSACTION_TOKEN_COLLISION"
    assert installer.state.transaction_id == TX and installer.state.phase == "staged"


def test_reserved_flags_are_rejected_before_any_download(tmp_path):
    agent, installer, _, _, calls, _, _ = setup_agent(tmp_path)
    frame = OtaCommandFrame(OtaCommand.PREPARE, TOKEN, Slot.B, flags=1).encode()
    result = agent.handle_command(frame)
    assert result.status == OtaStatus.ERROR and result.detail == 1
    assert calls == [] and installer.state.phase == "stable"


@pytest.mark.parametrize("failure,code", [
    (OSError, "APPLICATION_OBSERVATION_FAILED"),
    (ValueError, "APPLICATION_OBSERVATION_INVALID"),
    (TypeError, "APPLICATION_OBSERVATION_INVALID"),
])
def test_vehicle_adapter_failure_is_contained_and_sample_remains_retryable(tmp_path, failure, code):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    class Application:
        broken = True

        def apply_vehicle_status(self, sample):
            if self.broken:
                raise failure("application IPC failure")
            self.displayed_rpm = sample.rpm

    application = Application()
    agent.application_adapter = application
    sample = VehicleStatusFrame(450, 1000, Gear.DRIVE, 0, 7).encode()
    transport.incoming = [sample, sample]
    assert agent.poll_once(0) is None
    assert agent.last_error.code == code
    assert transport.sent == []
    application.broken = False
    assert agent.poll_once(0) is None
    assert application.displayed_rpm == 1000
    assert agent.last_error is None


@pytest.mark.parametrize("failure", [ValueError, TypeError])
def test_functional_adapter_value_type_failure_sets_diagnostic(tmp_path, failure):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    class BrokenApplication:
        def observe_functional_test(self, request):
            raise failure("invalid application observation")
    agent.application_adapter = BrokenApplication()
    transport.incoming = [FunctionalTestRequestFrame(450, 1000, Gear.DRIVE, 0, 1).encode()]
    assert agent.poll_once(0) is None
    assert agent.last_error.code == "APPLICATION_OBSERVATION_INVALID"
    assert transport.sent == []


def test_unrepresentable_observed_result_sets_diagnostic(tmp_path):
    agent, _, transport, _, _, _, _ = setup_agent(tmp_path)
    module = importlib.import_module("capstone_ota.agent.zonal")
    class BrokenApplication:
        def observe_functional_test(self, request):
            return module.ApplicationObservation(70000, 1000, Gear.DRIVE, 0)
    agent.application_adapter = BrokenApplication()
    transport.incoming = [FunctionalTestRequestFrame(450, 1000, Gear.DRIVE, 0, 1).encode()]
    assert agent.poll_once(0) is None
    assert agent.last_error.code == "APPLICATION_OBSERVATION_INVALID"
    assert transport.sent == []


def test_failed_rollback_suppresses_stale_trial_heartbeat_and_query_until_recovery(tmp_path):
    agent, installer, transport, _, _, services, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    agent.handle_command(command(OtaCommand.ACTIVATE, 1))
    assert agent.publish_heartbeat().state == ApplicationState.TRIAL
    services.healthy = False
    assert agent.handle_command(command(OtaCommand.ROLLBACK, 2)).status == OtaStatus.ERROR
    assert installer.state.phase == "rolling_back" and installer.state.active_slot == "B"
    assert installer.active_link.resolve() == installer.slots_dir / "A"
    journal = installer.state_path.read_bytes()
    sent = len(transport.sent)
    assert agent.publish_heartbeat() is None
    assert agent.last_error.code == "SLOT_STATE_MISMATCH"
    assert len(transport.sent) == sent
    for counter in (3, 4):
        status = agent.handle_command(command(OtaCommand.QUERY_STATUS, counter))
        assert status.status == OtaStatus.RECOVERY_FAILED and status.detail == 6
        assert status.slot == Slot.A
        assert agent.last_error.code == "SLOT_STATE_MISMATCH"
    assert installer.state_path.read_bytes() == journal
    services.healthy = True
    installer.recover_on_startup()
    assert agent.handle_command(command(OtaCommand.QUERY_STATUS, 5)).status == OtaStatus.ROLLED_BACK
    heartbeat = agent.publish_heartbeat()
    assert heartbeat.state == ApplicationState.STABLE and heartbeat.software_version == (0, 0, 0)
    assert heartbeat.counter == 1 and agent.last_error is None


def test_interrupted_activation_metadata_cannot_advertise_stale_stable_version(tmp_path, monkeypatch):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    agent.handle_command(command(OtaCommand.PREPARE))
    original = SlotState.save_atomic
    def interrupted_trial_write(state, path):
        if state.phase == "trial":
            raise OSError("power loss after selecting and restarting trial")
        return original(state, path)
    monkeypatch.setattr(SlotState, "save_atomic", interrupted_trial_write)
    assert agent.handle_command(command(OtaCommand.ACTIVATE, 1)).status == OtaStatus.ERROR
    assert installer.state.phase == "activating" and installer.state.active_slot == "A"
    assert installer.active_link.resolve() == installer.slots_dir / "B"
    journal = installer.state_path.read_bytes()
    assert agent.publish_heartbeat() is None
    assert agent.last_error.code == "SLOT_STATE_MISMATCH" and transport.sent == []
    status = agent.handle_command(command(OtaCommand.QUERY_STATUS, 2))
    assert status.status == OtaStatus.RECOVERY_FAILED and status.slot == Slot.B
    assert agent.last_error.code == "SLOT_STATE_MISMATCH"
    assert installer.state_path.read_bytes() == journal
    monkeypatch.setattr(SlotState, "save_atomic", original)
    installer.recover_on_startup()
    assert agent.handle_command(command(OtaCommand.QUERY_STATUS, 3)).status == OtaStatus.ROLLED_BACK
    assert agent.publish_heartbeat().software_version == (0, 0, 0)


@pytest.mark.parametrize("selection", ["different", "missing", "escaping", "missing_target"])
def test_invalid_selector_never_produces_stale_heartbeat_or_healthy_query(tmp_path, selection):
    agent, installer, transport, _, _, _, _ = setup_agent(tmp_path)
    journal = installer.state_path.read_bytes()
    installer.active_link.unlink()
    if selection == "different":
        (installer.slots_dir / "B").mkdir()
        installer.active_link.symlink_to("slots/B")
    elif selection == "escaping":
        installer.active_link.symlink_to(tmp_path / "source")
    elif selection == "missing_target":
        installer.active_link.symlink_to("slots/B")
    assert agent.publish_heartbeat() is None
    assert agent.last_error.code == "SLOT_STATE_MISMATCH" and transport.sent == []
    status = agent.handle_command(command(OtaCommand.QUERY_STATUS, token=0, slot=Slot.A))
    assert status.status == OtaStatus.RECOVERY_FAILED and status.detail == 6
    assert agent.last_error.code == "SLOT_STATE_MISMATCH"
    if selection == "different":
        assert status.slot == Slot.B
    assert installer.state_path.read_bytes() == journal
