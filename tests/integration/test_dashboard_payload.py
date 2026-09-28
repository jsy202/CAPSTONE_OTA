from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from capstone_ota.common.signing import generate_key_pair
from capstone_ota.publisher.bundle import BundleRequest, build_release


ROOT = Path(__file__).parents[2]
DASHBOARD = ROOT / "dashboard/volvo-digital-dash"


def test_fixture_has_executable_dashboard_binary():
    binary = ROOT / "tests/fixtures/dashboard-payload/bin/digital-dash"
    assert binary.is_file()
    assert os.access(binary, os.X_OK)


def test_make_payload_copies_binary_and_resources_without_host_links(tmp_path):
    binary = tmp_path / "arm-build/dash-app"
    binary.parent.mkdir()
    binary.write_bytes(b"ARM DASH BINARY\n")
    binary.chmod(0o755)
    resources = tmp_path / "runtime"
    resources.mkdir()
    (resources / "config.ini").write_text("[dash]\nstyle=240\n")
    output = tmp_path / "payload"

    result = subprocess.run(
        ["bash", str(DASHBOARD / "make-payload.sh"), str(binary), str(output), str(resources)],
        check=True, capture_output=True, text=True,
    )

    copied = output / "bin/digital-dash"
    assert copied.read_bytes() == binary.read_bytes()
    assert os.access(copied, os.X_OK)
    assert (output / "resources/config.ini").read_text() == "[dash]\nstyle=240\n"
    assert not any(path.is_symlink() for path in output.rglob("*"))
    assert str(tmp_path) not in copied.read_text()
    assert "--entrypoint bin/digital-dash" in result.stdout


def test_generated_payload_is_accepted_by_bundle_builder(tmp_path):
    fixture_binary = ROOT / "tests/fixtures/dashboard-payload/bin/digital-dash"
    payload = tmp_path / "payload"
    subprocess.run(
        ["bash", str(DASHBOARD / "make-payload.sh"), str(fixture_binary), str(payload)],
        check=True,
    )
    private_key = tmp_path / "sign.key"
    public_key = tmp_path / "sign.pub"
    generate_key_pair(private_key, public_key)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bundle = build_release(
        payload,
        tmp_path / "release",
        BundleRequest(
            device_id="cluster-pi-01",
            version="1.0.0",
            entrypoint="bin/digital-dash",
            artifact_base_url="https://localhost:8443",
            private_key_path=private_key,
            job_id="12345678-1234-4234-9234-123456789abc",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )
    assert bundle.archive_path.is_file()


def test_upstream_import_is_pinned_and_excludes_unrelated_trees():
    script = (DASHBOARD / "import-upstream.sh").read_text()
    provenance = (DASHBOARD / "UPSTREAM.md").read_text()
    assert "793452919127065536bcb7a08f98838fa963d75e" in script
    assert "https://github.com/whitfijs-jw/Volvo240-DigitalDash.git" in script
    assert "QtDash/VolvoDigitalDashModels" in script
    assert "buildroot" not in script.lower()
    assert "QtDash/Hardware" not in script
    assert "793452919127065536bcb7a08f98838fa963d75e" in provenance
    assert (DASHBOARD / "LICENSE.upstream").read_text().startswith("MIT License")
