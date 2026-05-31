import threading
import time
from typing import Optional

from connections import PLCConnection


class StatePoller:
    """Polls ADS state for a single PLC connection."""

    def __init__(self, connection: PLCConnection, stop_event: threading.Event, interval_seconds: float = 1.0):
        self.connection = connection
        self.stop_event = stop_event
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                state = self.connection.read_state()
                print(f"{self.connection.config.name} state: {state}")
            except Exception as exc:
                print(f"Error reading state from {self.connection.config.name}: {exc}")
            time.sleep(self.interval_seconds)

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)


class VariablePoller:
    """Polls ADS symbol values for a given PLC connection."""

    def __init__(self, connection: PLCConnection, symbol: str, stop_event: threading.Event, interval_seconds: float):
        self.connection = connection
        self.symbol = symbol
        self.stop_event = stop_event
        self.interval_seconds = interval_seconds
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                value = self.connection.read_by_name(self.symbol)
                print(f"[{self.connection.config.name} Variable] {self.symbol} = {value}")
            except Exception as exc:
                print(f"Error reading {self.symbol} from {self.connection.config.name}: {exc}")
            time.sleep(self.interval_seconds)

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)
