"""MQTT bus abstraction.

`Bus` is the small surface the backend needs: publish (with QoS/retain) and subscribe
to a topic filter. `InMemoryBus` implements MQTT topic-filter semantics (`+`, `#`),
retained messages and retained-delete-on-empty-payload, synchronously, for tests and
the simulator. `PahoBus` talks to a real broker (Mosquitto in docker-compose).
"""
from __future__ import annotations

import logging
import ssl
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

log = logging.getLogger(__name__)

Handler = Callable[[str, bytes], None]


def topic_matches(filt: str, topic: str) -> bool:
    """MQTT 3.1.1 §4.7 topic filter matching."""
    f = filt.split("/")
    t = topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)


class Bus(Protocol):
    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None: ...

    def subscribe(self, filt: str, handler: Handler, qos: int = 1) -> None: ...


@dataclass
class Published:
    topic: str
    payload: bytes
    qos: int
    retain: bool


class InMemoryBus:
    """Synchronous in-process broker with retained-message support."""

    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler]] = []
        self.retained: dict[str, bytes] = {}
        self.log: list[Published] = []
        self._lock = threading.RLock()

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        if isinstance(payload, str):
            payload = payload.encode()
        with self._lock:
            self.log.append(Published(topic, payload, qos, retain))
            if retain:
                if payload == b"":
                    self.retained.pop(topic, None)
                else:
                    self.retained[topic] = payload
            subs = [h for f, h in self._subs if topic_matches(f, topic)]
        for h in subs:
            h(topic, payload)

    def subscribe(self, filt: str, handler: Handler, qos: int = 1) -> None:
        with self._lock:
            self._subs.append((filt, handler))
            retained = [(t, p) for t, p in self.retained.items() if topic_matches(filt, t)]
        for t, p in retained:
            handler(t, p)

    def messages(self, filt: str) -> list[Published]:
        return [m for m in self.log if topic_matches(filt, m.topic)]


class PahoBus:
    """Real-broker implementation over paho-mqtt (v2 callback API)."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        client_id: str = "sentinel-backend",
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls_ca: Optional[str] = None,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        will: Optional[tuple[str, bytes, bool]] = None,
        connect_timeout: float = 5.0,
    ) -> None:
        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self._subs: list[tuple[str, Handler, int]] = []
        self._connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False)
        if username:
            self.client.username_pw_set(username, password)
        if tls_ca:
            self.client.tls_set(ca_certs=tls_ca, certfile=tls_cert, keyfile=tls_key, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        if will:
            self.client.will_set(will[0], will[1], qos=1, retain=will[2])
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()
        if not self._connected.wait(connect_timeout):
            self.client.loop_stop()
            raise ConnectionError(f"MQTT connect to {host}:{port} timed out")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.error("MQTT connect failed: %s", reason_code)
            return
        for filt, _, qos in self._subs:  # resubscribe after reconnect
            client.subscribe(filt, qos)
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        for filt, h, _ in list(self._subs):
            if topic_matches(filt, msg.topic):
                try:
                    h(msg.topic, msg.payload)
                except Exception:  # never kill the network thread
                    log.exception("handler failed for %s", msg.topic)

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        if qos > 0:
            info.wait_for_publish(timeout=5)

    def subscribe(self, filt: str, handler: Handler, qos: int = 1) -> None:
        self._subs.append((filt, handler, qos))
        self.client.subscribe(filt, qos)

    def close(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()
