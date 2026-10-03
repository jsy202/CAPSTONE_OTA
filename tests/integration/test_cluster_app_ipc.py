"""Component verification (A2/A5) of the real patched Qt Cluster application.

Runs the built dashboard binary offscreen and talks to it with the OTA
framework's own ApplicationIpc client - the exact client the zone agent uses.
Requires CAPSTONE_QT_DIR plus CAPSTONE_CLUSTER_APP (normal build) and, for
the fault cases, CAPSTONE_CLUSTER_FAULT_APP (qmake CAPSTONE_FAULT_SPEED_DIVISOR=10).
"""
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.common.can_protocol import FunctionalTestRequestFrame, Gear, VehicleStatusFrame


def _binary(name):
    path = os.environ.get(name)
    if not path or not os.environ.get("CAPSTONE_QT_DIR"):
        pytest.skip(f"{name} / CAPSTONE_QT_DIR not set: patched Qt application unavailable")
    return Path(path)


class ClusterProcess:
    def __init__(self, binary: Path, tmp: Path):
        self.socket = tmp / "ota.sock"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CAPSTONE_CLUSTER_IPC_SOCKET=str(self.socket),
                   CAPSTONE_APP_STATUS_FILE=str(tmp / "status.json"),
                   LD_LIBRARY_PATH=str(Path(os.environ["CAPSTONE_QT_DIR"]) / "lib"))
        self.log = (tmp / "cluster.log").open("w")
        self.process = subprocess.Popen([str(binary)], env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.socket.exists():
                try:
                    with socket.socket(socket.AF_UNIX) as probe:
                        probe.connect(str(self.socket))
                    return
                except OSError:
                    pass
            if self.process.poll() is not None:
                break
            time.sleep(0.1)
        self.close()
        raise RuntimeError(f"Cluster IPC did not come up; see {tmp / 'cluster.log'}")

    def client(self):
        return ApplicationIpc(self.socket, timeout_s=2.0)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()


@pytest.fixture
def normal(tmp_path):
    process = ClusterProcess(_binary("CAPSTONE_CLUSTER_APP"), tmp_path)
    yield process
    process.close()


@pytest.fixture
def faulty(tmp_path):
    process = ClusterProcess(_binary("CAPSTONE_CLUSTER_FAULT_APP"), tmp_path)
    yield process
    process.close()


CHALLENGE = FunctionalTestRequestFrame(650, 3000, Gear.DRIVE, 1, 7)


def test_normal_build_reports_interpreted_values_equal_to_challenge(normal):
    observed = normal.client().observe_functional_test(CHALLENGE)
    assert (observed.speed, observed.rpm, observed.gear, observed.warnings) == (650, 3000, Gear.DRIVE, 1)


def test_second_challenge_is_also_interpreted(normal):
    observed = normal.client().observe_functional_test(FunctionalTestRequestFrame(0, 800, Gear.PARK, 0, 8))
    assert (observed.speed, observed.rpm, observed.gear, observed.warnings) == (0, 800, Gear.PARK, 0)


def test_vehicle_sample_then_functional_reflects_functional_values(normal):
    client = normal.client()
    client.apply_vehicle_status(VehicleStatusFrame(800, 2500, Gear.DRIVE, 0, 1))
    observed = client.observe_functional_test(CHALLENGE)
    assert observed.speed == 650 and observed.rpm == 3000


def test_fault_build_misinterprets_speed_in_the_real_model(faulty):
    observed = faulty.client().observe_functional_test(CHALLENGE)
    assert observed.speed == 65            # 65.0 km/h requested, 6.5 km/h held by the speedometer model
    assert (observed.rpm, observed.gear, observed.warnings) == (3000, Gear.DRIVE, 1)
    log = Path(faulty.log.name).read_text()
    assert "DEMO/TEST FAULT INJECTION BUILD" in log


def test_malformed_and_silent_clients_do_not_stop_service(normal):
    silent = socket.socket(socket.AF_UNIX)
    silent.connect(str(normal.socket))
    silent.sendall(b'{"schema_version":1')
    with socket.socket(socket.AF_UNIX) as garbage:
        garbage.connect(str(normal.socket))
        garbage.sendall(b"\xff" * 5000)
    started = time.monotonic()
    observed = normal.client().observe_functional_test(CHALLENGE)
    assert observed.speed == 650 and time.monotonic() - started < 1.5
    silent.close()
    assert normal.process.poll() is None


def test_socket_is_owner_and_group_only(normal):
    assert oct(normal.socket.stat().st_mode & 0o777) == "0o660"
