import threading
from typing import Dict, Optional

from connections import PLCConnection
from mqtt_client import MQTTClient
from config import VariableGroupConfig


class VariableGroupPublisher:
    def __init__(
        self,
        group: VariableGroupConfig,
        connections_by_name: Dict[str, PLCConnection],
        mqtt_client: MQTTClient,
        stop_event: threading.Event,
    ) -> None:
        self.group = group
        self.connections_by_name = connections_by_name
        self.mqtt_client = mqtt_client
        self.stop_event = stop_event
        self._thread: Optional[threading.Thread] = None
        self._last_values: Dict[str, object] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def join(self, timeout: float = 1.0) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            values: Dict[str, object] = {}
            for var in self.group.variables:
                try:
                    plc_name = var.plc_name or self.group.plc_name
                    if not plc_name:
                        raise ValueError(
                            f"Missing plc_name for variable {var.name} ({var.symbol})"
                        )
                    conn = self.connections_by_name.get(plc_name)
                    if not conn:
                        raise ValueError(f"PLC not connected: {plc_name}")
                    values[var.name] = conn.poll_variable(var.symbol)
                except Exception as exc:
                    print(
                        f"Error reading {var.symbol} for group {self.group.topic} "
                        f"(var={var.name}, plc={var.plc_name or self.group.plc_name}): {exc}"
                    )
                    if self.group.allow_partial:
                        # Keep payload shape stable: failed reads become None.
                        values[var.name] = None
                        continue
                    # Strict mode: avoid partial payloads.
                    values = {}
                    break

            if values:
                changed = (not self._last_values) or any(
                    self._last_values.get(k) != v for k, v in values.items()
                )

                if changed:
                    payload = {self.group.name: values} if self.group.name else values
                    try:
                        self.mqtt_client.publish_json(self.group.topic, payload, retain=self.group.retain)
                        self._last_values = values
                    except Exception as exc:
                        print(f"MQTT publish error to {self.group.topic}: {exc}")

            if self.stop_event.wait(self.group.interval_seconds):
                break
