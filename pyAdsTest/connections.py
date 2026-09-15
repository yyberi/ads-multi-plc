import threading
from typing import Callable, List, Optional

import pyads

from config import PLCConfig


class PLCConnection:
    """Wrap pyads connection handling for a single PLC."""

    def __init__(self, config: PLCConfig, state_poll_interval: float = 1.0):
        self.config = config
        self._connection = pyads.Connection(config.ams, pyads.PORT_TC3PLC1, config.ip)
        self._lock = threading.Lock()
        self._open = False
        self._state_poll_interval = state_poll_interval
        self._state_polling_active = False
        self._state_poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_state = None
        self._state_change_callback: Optional[Callable] = None

    def open(self) -> None:
        if self._open:
            return
        self._connection.open()
        self._open = True
        print(f"Connected to {self.config.name} ({self.config.ip})")

    def close(self) -> None:
        if not self._open:
            return
        self.stop_state_polling()
        self._connection.close()
        self._open = False
        print(f"Closed {self.config.name} connection")

    def poll_state(self):
        with self._lock:
            return self._connection.read_state()

    def poll_variable(self, symbol: str):
        with self._lock:
            return self._connection.read_by_name(symbol)

    def set_state_poll_interval(self, interval_seconds: float) -> None:
        """Update the state polling interval."""
        self._state_poll_interval = interval_seconds

    def set_state_change_callback(self, callback: Optional[Callable[[str, object, object], None]]) -> None:
        """Set a callback function that is called when state changes.
        
        Callback signature: callback(plc_name: str, old_state, new_state)
        """
        self._state_change_callback = callback

    def start_state_polling(self) -> None:
        """Start background state polling thread."""
        if self._state_polling_active:
            return

        self._stop_event.clear()
        self._state_polling_active = True

        def _poll_loop():
            while not self._stop_event.is_set():
                try:
                    current_state = self.poll_state()
                    # print(f"{self.config.name} state: {current_state}")

                    # Check for state change and trigger callback
                    if self._state_change_callback:
                        if self._last_state is None or current_state != self._last_state:
                            self._state_change_callback(self.config.name, self._last_state, current_state)

                    self._last_state = current_state
                except Exception as exc:
                    print(f"Error reading state from {self.config.name}: {exc}")

                if self._stop_event.wait(self._state_poll_interval):
                    break

        self._state_poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        self._state_poll_thread.start()

    def stop_state_polling(self) -> None:
        """Stop background state polling thread."""
        if not self._state_polling_active:
            return

        self._stop_event.set()
        if self._state_poll_thread:
            self._state_poll_thread.join(timeout=2.0)
        self._state_polling_active = False
        self._state_poll_thread = None


def open_all(configs: List[PLCConfig]) -> List[PLCConnection]:
    connections: List[PLCConnection] = []
    for cfg in configs:
        conn = PLCConnection(cfg)
        try:
            conn.open()
            connections.append(conn)
        except Exception as exc:
            print(f"Failed to connect {cfg.name} ({cfg.ip}): {exc}")
    return connections


def close_all(connections: List[PLCConnection]) -> None:
    for conn in connections:
        try:
            conn.close()
        except Exception as exc:
            print(f"Error closing {conn.config.name}: {exc}")
