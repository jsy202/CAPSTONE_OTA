from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

from .config import AgentConfig
from .installer import ReleaseInstaller
from .service_manager import SystemdServiceManager
from .updater import AgentMessageRouter, UpdateAgent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capstone-ota-agent")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    config = AgentConfig.from_json(args.config)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.tls_set(
        ca_certs=str(config.ca_file),
        certfile=str(config.client_cert),
        keyfile=str(config.client_key),
    )
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    status_topic = f"capstone/{config.device_id}/ota/status"

    def status_sink(status):
        client.publish(
            status_topic,
            json.dumps(status, sort_keys=True, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    installer = ReleaseInstaller(config.install_root, SystemdServiceManager())
    agent = UpdateAgent(config, installer, status_sink)
    router = AgentMessageRouter(config.device_id, agent)
    pending: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=1)

    def worker():
        while True:
            item = pending.get()
            if item is None:
                return
            topic, payload = item
            try:
                router.handle(topic, payload)
            finally:
                pending.task_done()

    worker_thread = threading.Thread(target=worker, name="ota-worker", daemon=True)
    worker_thread.start()

    def on_connect(connected, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            connected.subscribe(router.topic, qos=1)

    def on_message(_connected, _userdata, message):
        if message.topic != router.topic:
            return
        try:
            pending.put_nowait((message.topic, bytes(message.payload)))
        except queue.Full:
            status_sink(
                {
                    "schema_version": 1,
                    "device_id": config.device_id,
                    "job_id": "unknown",
                    "version": "unknown",
                    "stage": "failed",
                    "progress": 0,
                    "success": False,
                    "error": {"code": "BUSY", "message": "another update is already queued"},
                }
            )

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.broker_host, config.broker_port, 60)
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass
    finally:
        pending.put(None)
        worker_thread.join(timeout=5)
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
