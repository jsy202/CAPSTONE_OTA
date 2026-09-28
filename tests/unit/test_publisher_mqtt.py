import json

import pytest

from capstone_ota.common.errors import OtaError
from capstone_ota.publisher.mqtt import PublisherMqtt


class PublishInfo:
    def __init__(self, published=True):
        self.rc = 0
        self._published = published
        self.wait_timeout = None

    def wait_for_publish(self, timeout):
        self.wait_timeout = timeout

    def is_published(self):
        return self._published


class FakeClient:
    def __init__(self, published=True, connect_reason=0):
        self.calls = []
        self.info = PublishInfo(published)
        self.connect_reason = connect_reason
        self.connected = False
        self.on_connect = None
        self.on_message = None

    def tls_set(self, **kwargs):
        self.calls.append(("tls_set", kwargs))

    def connect(self, host, port, keepalive):
        self.calls.append(("connect", host, port, keepalive))

    def loop_start(self):
        self.calls.append(("loop_start",))
        if self.on_connect is not None:
            self.connected = self.connect_reason == 0
            self.on_connect(self, None, None, self.connect_reason, None)

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def publish(self, topic, payload, qos, retain):
        assert self.connected
        self.calls.append(("publish", topic, payload, qos, retain))
        return self.info

    def subscribe(self, topic, qos):
        assert self.connected
        self.calls.append(("subscribe", topic, qos))
        return (0, 1)


def publisher(fake):
    return PublisherMqtt(
        host="broker.local",
        port=8883,
        ca_file="/etc/certs/ca.pem",
        cert_file="/etc/certs/publisher.pem",
        key_file="/etc/certs/publisher.key",
        client_factory=lambda: fake,
    )


def test_publish_uses_mutual_tls_device_topic_qos_one_and_retained_message():
    fake = FakeClient()
    client = publisher(fake)
    command = {"job_id": "job-1", "version": "1.2.3"}

    client.publish_command("cluster-pi-01", command)

    assert fake.calls[0] == (
        "tls_set",
        {
            "ca_certs": "/etc/certs/ca.pem",
            "certfile": "/etc/certs/publisher.pem",
            "keyfile": "/etc/certs/publisher.key",
        },
    )
    publish_call = next(call for call in fake.calls if call[0] == "publish")
    assert publish_call[1] == "capstone/cluster-pi-01/ota/command"
    assert json.loads(publish_call[2]) == command
    assert publish_call[3:] == (1, True)
    assert fake.info.wait_timeout == 10
    assert fake.calls[-2:] == [("loop_stop",), ("disconnect",)]


def test_publish_rejects_invalid_device_id_without_connecting():
    fake = FakeClient()
    with pytest.raises(OtaError) as error:
        publisher(fake).publish_command("../all-devices", {"version": "1.0.0"})
    assert error.value.code == "DEVICE_ID_INVALID"
    assert fake.calls == []


def test_publish_times_out_without_acknowledgement():
    fake = FakeClient(published=False)
    with pytest.raises(OtaError) as error:
        publisher(fake).publish_command("cluster-pi-01", {"version": "1.0.0"})
    assert error.value.code == "MQTT_ACK_TIMEOUT"


def test_watch_subscribes_only_to_selected_device_status_topic():
    fake = FakeClient()
    client = publisher(fake)

    client.start_status_watch("cluster-pi-01", lambda _message: None)

    assert ("subscribe", "capstone/cluster-pi-01/ota/status", 1) in fake.calls
    assert all("#" not in str(call) and "+" not in str(call) for call in fake.calls)
    client.close()


def test_broker_rejection_is_reported_before_publish():
    fake = FakeClient(connect_reason=5)
    with pytest.raises(OtaError) as error:
        publisher(fake).publish_command("cluster-pi-01", {"version": "1.0.0"})
    assert error.value.code == "MQTT_CONNECT_FAILED"
    assert not any(call[0] == "publish" for call in fake.calls)
