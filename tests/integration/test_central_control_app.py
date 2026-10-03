"""Protocol conformance (A3) of the Central Control application against the
real OTA framework clients and codecs."""
import os
import socket
import stat
import threading
import time

import pytest

from capstone_ota.common.application_ipc import ApplicationIpc
from capstone_ota.common.errors import OtaError
from tests.unit.test_central_control import C, Transport, identity_file


class Running:
    def __init__(self, tmp_path):
        self.app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
        self.path = tmp_path / "ota.sock"
        self.server = C.MaintenanceServer(self.path, self.app)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            self.server.service(0.02)

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.server.close()


@pytest.fixture
def running(tmp_path):
    r = Running(tmp_path)
    yield r
    r.close()


def test_real_application_ipc_client_toggles_maintenance(running):
    client = ApplicationIpc(running.path)
    client.set_maintenance(True, timeout_s=2)
    assert running.app.maintenance is True
    client.set_maintenance(False, timeout_s=2)
    assert running.app.maintenance is False


def test_socket_is_owner_and_group_only(running):
    mode = stat.S_IMODE(os.stat(running.path).st_mode)
    assert mode == 0o660


def test_silent_client_does_not_block_others(running):
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.connect(str(running.path))
    silent.sendall(b'{"schema_version":1')  # partial message, never finished
    started = time.monotonic()
    ApplicationIpc(running.path).set_maintenance(True, timeout_s=2)
    assert time.monotonic() - started < 1.0
    silent.close()


def test_partial_client_is_dropped_after_deadline(running, monkeypatch):
    lingering = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    lingering.connect(str(running.path))
    lingering.settimeout(4)
    assert lingering.recv(10) == b""  # closed by the 2 s per-connection deadline
    lingering.close()


def test_unknown_operation_is_rejected_by_the_real_client_contract(running):
    client = ApplicationIpc(running.path)
    with pytest.raises(OtaError) as error:
        client._call("reboot", {}, 2)
    assert error.value.code == "APPLICATION_IPC_REJECTED"


@pytest.mark.parametrize("kind", ["file", "symlink", "fifo"])
def test_server_refuses_to_replace_non_socket_path(tmp_path, kind):
    path = tmp_path / "ota.sock"
    victim = tmp_path / "victim"
    victim.write_text("keep")
    if kind == "file":
        path.write_text("keep")
    elif kind == "symlink":
        path.symlink_to(victim)
    else:
        os.mkfifo(path)
    app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
    with pytest.raises(RuntimeError):
        C.MaintenanceServer(path, app)
    assert os.path.lexists(path) and victim.read_text() == "keep"


def test_stale_socket_is_replaced(tmp_path):
    path = tmp_path / "ota.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    app = C.CentralControl(Transport(), identity_file(tmp_path), start=0.0)
    server = C.MaintenanceServer(path, app)
    server.close()
    assert not os.path.lexists(path)
