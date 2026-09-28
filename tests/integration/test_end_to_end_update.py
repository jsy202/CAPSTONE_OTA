from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

from capstone_ota.agent.config import AgentConfig
from capstone_ota.agent.installer import ReleaseInstaller
from capstone_ota.agent.updater import UpdateAgent
from capstone_ota.common.signing import generate_key_pair
from capstone_ota.publisher.bundle import BundleRequest, build_release
from capstone_ota.publisher.cli import build_command
from capstone_ota.publisher.http_server import create_https_server
from tests.integration.test_https_artifact_server import server_certificate


class SequencedService:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def restart_and_wait_healthy(self, unit, timeout):
        self.calls.append((unit, timeout))
        return self.results.pop(0)


def _payload(root, label):
    payload = root / f"payload-{label}"
    binary = payload / "bin/digital-dash"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!/bin/sh\necho {label}\n")
    binary.chmod(0o755)
    return payload


def _bundle(tmp_path, release_root, signing_key, base_url, version):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return build_release(
        _payload(tmp_path, version),
        release_root,
        BundleRequest(
            device_id="cluster-pi-01",
            version=version,
            entrypoint="bin/digital-dash",
            artifact_base_url=base_url,
            private_key_path=signing_key,
            job_id=str(uuid.uuid4()),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )


def _command(bundle, base_url):
    return json.dumps(build_command(bundle, base_url)).encode()


def test_real_https_update_tamper_rejection_and_health_rollback(tmp_path):
    release_root = tmp_path / "served-releases"
    release_root.mkdir()
    ca, cert, https_key = server_certificate(tmp_path)
    signing_key = tmp_path / "update.key"
    public_key = tmp_path / "update.pub"
    generate_key_pair(signing_key, public_key)
    server = create_https_server(release_root, "127.0.0.1", 0, cert, https_key)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"https://127.0.0.1:{server.server_port}/releases"

    install_root = tmp_path / "installed"
    old_binary = install_root / "releases/1.0.0/bin/digital-dash"
    old_binary.parent.mkdir(parents=True)
    old_binary.write_text("#!/bin/sh\necho old\n")
    old_binary.chmod(0o755)
    install_root.joinpath("staging").mkdir()
    install_root.joinpath("current").symlink_to("releases/1.0.0")
    config = AgentConfig(
        device_id="cluster-pi-01",
        broker_host="localhost",
        broker_port=8883,
        ca_file=ca,
        client_cert=cert,
        client_key=https_key,
        public_key=public_key,
        install_root=install_root,
        state_file=tmp_path / "state.json",
        download_max_bytes=10_000_000,
        archive_max_files=100,
        archive_max_bytes=10_000_000,
    )
    service = SequencedService([True, False, True])
    statuses = []
    agent = UpdateAgent(config, ReleaseInstaller(install_root, service), statuses.append)

    try:
        release_101 = _bundle(tmp_path, release_root, signing_key, base_url, "1.0.1")
        success = agent.handle_command(_command(release_101, base_url))
        assert success.success is True
        assert install_root.joinpath("current").resolve().name == "1.0.1"

        tampered = _bundle(tmp_path, release_root, signing_key, base_url, "1.0.2")
        tampered.archive_path.write_bytes(tampered.archive_path.read_bytes() + b"tampered")
        rejected = agent.handle_command(_command(tampered, base_url))
        assert rejected.success is False
        assert rejected.error_code in {"DOWNLOAD_SIZE_MISMATCH", "ARTIFACT_HASH_MISMATCH"}
        assert install_root.joinpath("current").resolve().name == "1.0.1"

        unhealthy = _bundle(tmp_path, release_root, signing_key, base_url, "1.0.2")
        rolled_back = agent.handle_command(_command(unhealthy, base_url))
        assert rolled_back.stage == "rollback"
        assert rolled_back.rolled_back is True
        assert install_root.joinpath("current").resolve().name == "1.0.1"
        assert [status["stage"] for status in statuses].count("success") == 1
        assert statuses[-1]["stage"] == "rollback"
    finally:
        server.shutdown()
        server_thread.join()
        server.server_close()
