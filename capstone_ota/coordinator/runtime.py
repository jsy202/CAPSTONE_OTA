"""Coordinator process wiring: MQTT command intake, private HTTPS cache, CAN.

Startup order is CAN, HTTPS, durable recovery, worker, then MQTT subscription,
so no command is accepted before an interrupted transaction is resolved. A
RECOVERY_FAILED journal never subscribes. One worker serializes commands; an
unexpected worker exception is surfaced through check() so systemd restarts
the service instead of silently dropping updates.
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from uuid import UUID

from capstone_ota.agent.archive import ArchiveLimits, safe_extract_tar
from capstone_ota.agent.downloader import download_https
from capstone_ota.agent.updater import fetch_https_bytes
from capstone_ota.common.errors import OtaError
from capstone_ota.common.manifest import ReleaseManifest
from capstone_ota.common.signing import verify_document_signature, verify_manifest_signature
from capstone_ota.common.vehicle_bundle import EcuTarget, VehicleBundleManifest

from .core import PreparedArtifact


_CACHE_PATH = re.compile(r"/transactions/([0-9a-f]{8})/cluster/(transaction\.json|manifest\.json|manifest\.sig|artifact\.tar\.gz)")
_DEVICES = {"central-control": "central-pi-01", "digital-cluster": "cluster-pi-02"}
_COMMAND_FIELDS = {"schema_version", "command", "device_id", "transaction_id", "bundle_version",
                   "bundle_manifest_url", "bundle_signature_url"}


def cache_handler(cache_root: Path):
    """Serve only the four exact per-transaction Cluster files; no listing."""
    root = Path(cache_root).resolve()

    class CacheHandler(BaseHTTPRequestHandler):
        def _path(self) -> Path | None:
            match = _CACHE_PATH.fullmatch(self.path)
            if match is None:
                return None
            candidate = root / "transactions" / match[1] / "cluster" / match[2]
            if any(p.is_symlink() for p in (candidate, *candidate.parents) if p.is_relative_to(root)):
                return None
            if not candidate.is_file() or candidate.resolve().parent != root / "transactions" / match[1] / "cluster":
                return None
            return candidate

        def _send(self, body: bool) -> None:
            path = self._path()
            if path is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile, 64 * 1024)

        def do_GET(self) -> None:
            self._send(True)

        def do_HEAD(self) -> None:
            self._send(False)

        def log_message(self, _format, *_args) -> None:
            return

    return CacheHandler


def create_cache_server(cache_root: Path, bind: str, port: int, cert: Path, key: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), cache_handler(cache_root))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


@dataclass(frozen=True)
class SignedRelease:
    """Independently signed per-ECU release metadata fetched from the laptop."""
    transaction_token: int
    manifest: bytes
    signature: bytes


class ArtifactCache:
    """ArtifactPreparer/ArtifactPublisher backed by cache_root.

    transactions/<token>/<role>/ holds the original manifest, signature and
    archive; extract/<token>/<role>/ holds the verified staging tree. Only the
    Cluster directory is reachable through cache_handler.
    """
    def __init__(self, cache_root: Path, *, ca_file: Path, public_key: Path, max_bytes: int, limits: ArchiveLimits,
                 current_version: Callable[[str], str | None], downloader: Callable = download_https,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.root = Path(cache_root)
        self.ca_file, self.public_key, self.max_bytes, self.limits = ca_file, public_key, max_bytes, limits
        self.current_version, self.downloader, self.now = current_version, downloader, now

    def _dir(self, kind: str, token: int, role: str) -> Path:
        return self.root / kind / f"{token:08x}" / ("cluster" if role == "digital-cluster" else "central")

    def prepare(self, target: EcuTarget, artifact: SignedRelease, *, timeout_s: float) -> PreparedArtifact:
        release = ReleaseManifest.from_bytes(artifact.manifest)
        verify_manifest_signature(release, artifact.signature, self.public_key)
        release.validate_for(_DEVICES[target.ecu_id], self.current_version(target.ecu_id), self.now())
        if (release.version, release.entrypoint, release.artifact_url, release.artifact_size, release.artifact_sha256) != (
                target.software_version, target.entrypoint, target.artifact_url, target.artifact_size, target.artifact_sha256):
            raise OtaError("RELEASE_BINDING_INVALID", "signed release differs from vehicle target")
        files = self._dir("transactions", artifact.transaction_token, target.ecu_id)
        extract = self._dir("extract", artifact.transaction_token, target.ecu_id)
        for directory in (files, extract):
            if directory.exists():
                shutil.rmtree(directory)
        files.mkdir(parents=True, mode=0o750)
        archive = files / "artifact.tar.gz"
        result = self.downloader(release.artifact_url, archive, self.ca_file, release.artifact_size, self.max_bytes)
        if result.size != release.artifact_size or result.sha256 != release.artifact_sha256:
            raise OtaError("ARTIFACT_HASH_MISMATCH", "downloaded archive differs from signed release")
        safe_extract_tar(archive, extract, self.limits)
        (files / "manifest.json").write_bytes(artifact.manifest)
        (files / "manifest.sig").write_bytes(artifact.signature)
        return PreparedArtifact(target.ecu_id, release.version, release.entrypoint, archive, extract)

    def publish(self, bundle: VehicleBundleManifest, prepared: Mapping[str, PreparedArtifact], *, timeout_s: float) -> None:
        cluster = self._dir("transactions", bundle.transaction_token, "digital-cluster")
        release = ReleaseManifest.from_bytes((cluster / "manifest.json").read_bytes())
        binding = {"schema_version": 1, "transaction_id": bundle.transaction_id, "release_job_id": str(UUID(release.job_id))}
        temporary = cluster / ".transaction.json.tmp"
        temporary.write_text(json.dumps(binding, sort_keys=True))
        temporary.replace(cluster / "transaction.json")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class VehicleCommandHandler:
    """Validate one MQTT vehicle-update command, fetch signed metadata, execute.

    Expected OTA failures before execute are published as status, not raised.
    The coordinator itself publishes every durable phase transition.
    """
    def __init__(self, coordinator, *, device_id: str, ca_file: Path, public_key: Path,
                 publish_status: Callable[[dict], None], fetch_bytes: Callable = fetch_https_bytes):
        self.coordinator, self.device_id, self.ca_file, self.public_key = coordinator, device_id, ca_file, public_key
        self.publish_status, self.fetch_bytes = publish_status, fetch_bytes

    def _reject(self, command: object, exc: OtaError) -> None:
        tx = command.get("transaction_id") if isinstance(command, dict) else None
        self.publish_status({"schema_version": 1, "device_id": self.device_id,
                             "transaction_id": tx if isinstance(tx, str) else None, "phase": "REJECTED",
                             "last_error": {"code": exc.code, "message": exc.message}})

    def __call__(self, raw: bytes) -> None:
        command: object = None
        try:
            try:
                command = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            except (UnicodeError, ValueError) as exc:
                raise OtaError("COMMAND_INVALID", "command must be strict UTF-8 JSON") from exc
            if (not isinstance(command, dict) or set(command) != _COMMAND_FIELDS
                    or command["schema_version"] != 1 or type(command["schema_version"]) is not int
                    or command["command"] != "vehicle-update"):
                raise OtaError("COMMAND_INVALID", "unsupported vehicle command")
            if command["device_id"] != self.device_id:
                raise OtaError("WRONG_DEVICE", "command targets another coordinator")
            raw_bundle = self.fetch_bytes(command["bundle_manifest_url"], self.ca_file, 65536)
            signature = self.fetch_bytes(command["bundle_signature_url"], self.ca_file, 64)
            bundle = VehicleBundleManifest.from_bytes(raw_bundle)
            verify_document_signature(bundle, signature, self.public_key)
            if (bundle.transaction_id, bundle.bundle_version) != (command["transaction_id"], command["bundle_version"]):
                raise OtaError("COMMAND_BINDING_INVALID", "command differs from signed bundle")
            artifacts = {target.ecu_id: SignedRelease(bundle.transaction_token,
                            self.fetch_bytes(target.release_manifest_url, self.ca_file, 65536),
                            self.fetch_bytes(target.release_signature_url, self.ca_file, 64))
                         for target in bundle.targets}
        except OtaError as exc:
            self._reject(command, exc)
            return
        try:
            self.coordinator.execute(bundle, artifacts)
        except OtaError as exc:
            # RECOVERY_REQUIRED/collisions leave the journal untouched.
            self._reject(command, exc)


class CoordinatorMqtt:
    """Mutual-TLS subscriber for capstone/<device>/ota/command; retained status."""
    def __init__(self, *, device_id: str, host: str, port: int, ca_file: Path, cert_file: Path, key_file: Path,
                 client_factory: Callable | None = None):
        import paho.mqtt.client as mqtt
        self._mqtt = mqtt
        self.client = (client_factory or (lambda: mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)))()
        self.client.tls_set(ca_certs=str(ca_file), certfile=str(cert_file), keyfile=str(key_file))
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.command_topic = f"capstone/{device_id}/ota/command"
        self.status_topic = f"capstone/{device_id}/ota/status"
        self.host, self.port = host, port

    def publish_status(self, event: dict) -> None:
        self.client.publish(self.status_topic, json.dumps(event, sort_keys=True, separators=(",", ":")),
                            qos=1, retain=True)

    def start(self, receive: Callable[[bytes], None]) -> None:
        def on_connect(client, _userdata, _flags, reason_code, _properties):
            if reason_code == 0:
                client.subscribe(self.command_topic, qos=1)

        def on_message(_client, _userdata, message):
            if message.topic == self.command_topic:
                receive(bytes(message.payload))

        self.client.on_connect, self.client.on_message = on_connect, on_message
        self.client.connect_async(self.host, self.port, 60)
        self.client.loop_start()

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class CoordinatorRuntime:
    def __init__(self, coordinator, mqtt, server, bus, handle: Callable[[bytes], None], *,
                 on_busy: Callable[[], None] | None = None):
        self.coordinator, self.mqtt, self.server, self.bus = coordinator, mqtt, server, bus
        self.handle, self.on_busy = handle, on_busy
        self.pending: queue.Queue = queue.Queue(maxsize=1)
        self.worker = threading.Thread(target=self._work, name="coordinator-worker", daemon=True)
        self._https: threading.Thread | None = None
        self._started = set()
        self.failure: BaseException | None = None

    def _work(self) -> None:
        while (raw := self.pending.get()) is not None:
            try:
                self.handle(raw)
            except BaseException as exc:
                self.failure = exc
                return

    def receive(self, raw: bytes) -> None:
        try:
            self.pending.put_nowait(raw)
        except queue.Full:
            if self.on_busy is not None:
                self.on_busy()

    def start(self) -> None:
        self.bus.start()
        self._started.add("bus")
        self._https = threading.Thread(target=self.server.serve_forever, name="cache-https", daemon=True)
        self._https.start()
        self._started.add("https")
        self.coordinator.recover_on_startup()
        state = self.coordinator.state
        if state.phase == "RECOVERY_FAILED":
            raise OtaError("RECOVERY_REQUIRED", "failed recovery requires manual intervention before new commands")
        self.worker.start()
        self.mqtt.start(self.receive)
        self._started.add("mqtt")

    def check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if self.worker.ident is not None and not self.worker.is_alive():
            raise OtaError("WORKER_STOPPED", "coordinator worker stopped")
        self.bus.check()

    def close(self) -> None:
        if "mqtt" in self._started:
            self.mqtt.close()
        if self.worker.is_alive():
            # Sentinel after any in-flight command; the journal stays authoritative.
            self.pending.put(None)
            self.worker.join()
        if "https" in self._started:
            self.server.shutdown()
            self._https.join(2)
        self.server.server_close()
        if "bus" in self._started:
            self.bus.close()
