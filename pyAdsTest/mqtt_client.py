import json
import threading
from dataclasses import dataclass
from typing import Optional

import paho.mqtt.client as mqtt


@dataclass(frozen=True)
class MQTTConfig:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    client_id: str
    keepalive: int = 60
    qos: int = 0
    retain: bool = False


class MQTTClient:
    def __init__(self, config: MQTTConfig):
        self._config = config
        self._client = mqtt.Client(client_id=config.client_id)
        if config.username:
            self._client.username_pw_set(config.username, config.password)
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        self._client.connect(self._config.host, self._config.port, self._config.keepalive)
        self._client.loop_start()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    def publish_json(self, topic: str, payload_obj, *, retain: Optional[bool] = None) -> None:
        payload = json.dumps(payload_obj, ensure_ascii=False)
        retain_flag = self._config.retain if retain is None else retain
        with self._lock:
            self._client.publish(
                topic,
                payload,
                qos=self._config.qos,
                retain=retain_flag,
            )
