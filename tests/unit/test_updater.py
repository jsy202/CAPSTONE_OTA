import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from capstone_ota.agent.config import AgentConfig
from capstone_ota.agent.downloader import DownloadResult
from capstone_ota.agent.installer import ReleaseInstaller
from capstone_ota.agent.updater import UpdateAgent
from capstone_ota.common.signing import generate_key_pair
from capstone_ota.publisher.bundle import BundleRequest, build_release


class HealthyService:
    def __init__(self, results=(True,)):
        self.results = iter(results)

    def restart_and_wait_healthy(self, _unit, _timeout):
        return next(self.results)


def setup_update(tmp_path, version="1.0.1", current_version="1.0.0", service_results=(True,)):
    keys = tmp_path / "keys"
    keys.mkdir()
    private = keys / "private.pem"
    public = keys / "public.pem"
    generate_key_pair(private, public)
    for name in ("ca.pem", "client.pem", "client.key"):
        (keys / name).write_text("fixture")
    payload = tmp_path / "payload"
    binary = payload / "bin" / "digital-dash"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho {version}\n")
    binary.chmod(0o755)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bundle = build_release(
        payload,
        tmp_path / "published",
        BundleRequest(
            device_id="cluster-pi-01",
            version=version,
            entrypoint="bin/digital-dash",
            artifact_base_url="https://laptop.local:8443/releases",
            private_key_path=private,
            job_id="550e8400-e29b-41d4-a716-446655440000",
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        ),
    )
    install_root = tmp_path / "opt" / "capstone"
    (install_root / "releases").mkdir(parents=True)
    (install_root / "staging").mkdir()
    if current_version:
        current = install_root / "releases" / current_version
        current_binary = current / "bin" / "digital-dash"
        current_binary.parent.mkdir(parents=True)
        current_binary.write_text("#!/bin/sh\nexit 0\n")
        current_binary.chmod(0o755)
        (install_root / "current").symlink_to(current.relative_to(install_root))
    config = AgentConfig(
        device_id="cluster-pi-01",
        broker_host="laptop.local",
        broker_port=8883,
        ca_file=keys / "ca.pem",
        client_cert=keys / "client.pem",
        client_key=keys / "client.key",
        public_key=public,
        install_root=install_root,
        state_file=tmp_path / "var" / "state.json",
        download_max_bytes=100_000,
        archive_max_files=100,
        archive_max_bytes=100_000,
    )
    statuses = []
    calls = {"fetch": 0, "download": 0}

    def fetch(url, _ca, _max_bytes):
        calls["fetch"] += 1
        if url.endswith(".manifest.json"):
            return bundle.manifest_path.read_bytes()
        if url.endswith(".manifest.sig"):
            return bundle.signature_path.read_bytes()
        raise AssertionError(url)

    def download(_url, destination, _ca, expected_size, _max_bytes):
        calls["download"] += 1
        shutil.copyfile(bundle.archive_path, destination)
        content = destination.read_bytes()
        return DownloadResult(destination, len(content), hashlib.sha256(content).hexdigest())

    installer = ReleaseInstaller(install_root, HealthyService(service_results))
    agent = UpdateAgent(
        config,
        installer,
        status_sink=statuses.append,
        fetch_bytes=fetch,
        artifact_downloader=download,
        now=lambda: now,
    )
    command = json.dumps(
        {
            "schema_version": 1,
            "job_id": bundle.manifest.job_id,
            "device_id": "cluster-pi-01",
            "version": version,
            "manifest_url": f"https://laptop.local:8443/releases/{version}.manifest.json",
            "signature_url": f"https://laptop.local:8443/releases/{version}.manifest.sig",
        }
    ).encode()
    return agent, command, statuses, calls, config, bundle


def test_successful_update_reports_exact_stage_order_and_persists_state(tmp_path):
    agent, command, statuses, _calls, config, _bundle = setup_update(tmp_path)

    result = agent.handle_command(command)

    assert [status["stage"] for status in statuses] == [
        "received", "downloading", "verifying", "downloading", "staging",
        "activating", "health_check", "success",
    ]
    assert result.success is True
    assert result.stage == "success"
    state = json.loads(config.state_file.read_text())
    assert state["current_version"] == "1.0.1"
    assert state["previous_version"] == "1.0.0"
    assert state["active_job"] is None
    assert config.install_root.joinpath("current").resolve().name == "1.0.1"


def test_duplicate_job_republishes_terminal_result_without_download(tmp_path):
    agent, command, statuses, calls, _config, _bundle = setup_update(tmp_path)
    first = agent.handle_command(command)
    status_count = len(statuses)

    second = agent.handle_command(command)

    assert second == first
    assert calls["download"] == 1
    assert len(statuses) == status_count + 1
    assert statuses[-1]["stage"] == "success"


def test_modified_artifact_is_rejected_before_staging(tmp_path):
    agent, command, statuses, _calls, config, bundle = setup_update(tmp_path)
    bundle.archive_path.write_bytes(bundle.archive_path.read_bytes() + b"tampered")

    result = agent.handle_command(command)

    assert result.success is False
    assert result.error_code in {"DOWNLOAD_SIZE_MISMATCH", "ARTIFACT_HASH_MISMATCH"}
    assert statuses[-1]["stage"] == "failed"
    assert config.install_root.joinpath("current").resolve().name == "1.0.0"


def test_wrong_device_is_rejected_before_artifact_download(tmp_path):
    agent, command, statuses, calls, _config, _bundle = setup_update(tmp_path)
    data = json.loads(command)
    data["device_id"] = "other-pi"

    result = agent.handle_command(json.dumps(data).encode())

    assert result.error_code == "WRONG_DEVICE"
    assert calls["download"] == 0
    assert statuses[-1]["stage"] == "failed"


def test_unhealthy_release_reports_rollback_and_restores_current(tmp_path):
    agent, command, statuses, _calls, config, _bundle = setup_update(
        tmp_path, service_results=(False, True)
    )

    result = agent.handle_command(command)

    assert result.success is False
    assert result.rolled_back is True
    assert result.stage == "rollback"
    assert statuses[-1]["stage"] == "rollback"
    assert config.install_root.joinpath("current").resolve().name == "1.0.0"


def test_current_or_older_release_is_rejected(tmp_path):
    agent, command, statuses, calls, config, _bundle = setup_update(
        tmp_path, version="1.0.0", current_version="1.0.0"
    )
    state = {
        "schema_version": 1,
        "current_version": "1.0.0",
        "previous_version": None,
        "completed_jobs": {},
        "failed_versions": [],
        "active_job": None,
    }
    config.state_file.parent.mkdir(parents=True)
    config.state_file.write_text(json.dumps(state))

    result = agent.handle_command(command)

    assert result.error_code == "ROLLBACK_REJECTED"
    assert calls["download"] == 0
    assert statuses[-1]["stage"] == "failed"
