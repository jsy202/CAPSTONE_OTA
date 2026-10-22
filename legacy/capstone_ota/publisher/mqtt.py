from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Callable, Mapping

import paho.mqtt.client as mqtt

from capstone_ota.common.errors import OtaError


_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _new_client():
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


class PublisherMqtt:
    def __init__(
        self,
        host: str,
        port: int,
        ca_file: str | Path,
        cert_file: str | Path,
        key_file: str | Path,
        client_factory: Callable = _new_client,
        connection_timeout: float = 10,
    ):
        self.host = host
        self.port = port
        self.ca_file = str(ca_file)
        self.cert_file = str(cert_file)
        self.key_file = str(key_file)
        self._client_factory = client_factory
        self.connection_timeout = connection_timeout
        self._client = None

    @staticmethod
    def _validate_device(device_id: str) -> None:
        if not _DEVICE_RE.fullmatch(device_id):
            raise OtaError("DEVICE_ID_INVALID", "invalid MQTT device identifier")

    def _connect(self):
        client = self._client_factory()
        connected = threading.Event()
        failure: list[object] = []

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            if reason_code != 0:
                failure.append(reason_code)
            connected.set()

        client.on_connect = on_connect
        client.tls_set(
            ca_certs=self.ca_file,
            certfile=self.cert_file,
            keyfile=self.key_file,
        )
        try:
            client.connect(self.host, self.port, 60)
            client.loop_start()
        except (OSError, mqtt.WebsocketConnectionError) as exc:
            raise OtaError("MQTT_CONNECT_FAILED", "cannot connect to MQTT broker") from exc
        if not connected.wait(self.connection_timeout) or failure:
            client.loop_stop()
            client.disconnect()
            raise OtaError("MQTT_CONNECT_FAILED", "MQTT broker rejected or timed out")
        self._client = client
        return client

    def publish_command(self, device_id: str, command: Mapping[str, object]) -> None:
        self._validate_device(device_id)
        client = self._connect()
        try:
            payload = json.dumps(command, sort_keys=True, separators=(",", ":"))
            info = client.publish(
                f"capstone/{device_id}/ota/command",
                payload,
                qos=1,
                retain=True,
            )
            if getattr(info, "rc", mqtt.MQTT_ERR_SUCCESS) != mqtt.MQTT_ERR_SUCCESS:
                raise OtaError("MQTT_PUBLISH_FAILED", "broker rejected update command")
            info.wait_for_publish(timeout=10)
            if not info.is_published():
                raise OtaError("MQTT_ACK_TIMEOUT", "broker did not acknowledge update command")
        finally:
            self.close()

    def start_status_watch(
        self, device_id: str, on_status: Callable[[dict[str, object]], None]
    ) -> None:
        self._validate_device(device_id)
        client = self._connect()

        def receive(_client, _userdata, message):
            if message.topic != f"capstone/{device_id}/ota/status":
                return
            try:
                decoded = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if isinstance(decoded, dict):
                on_status(decoded)

        client.on_message = receive
        result, _message_id = client.subscribe(
            f"capstone/{device_id}/ota/status", qos=1
        )
        if result != mqtt.MQTT_ERR_SUCCESS:
            self.close()
            raise OtaError("MQTT_SUBSCRIBE_FAILED", "cannot subscribe to device status")

    def close(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        client.loop_stop()
        client.disconnect()
