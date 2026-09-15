import threading
from typing import List

from config import VariablePollConfig, load_settings
from connections import PLCConnection, close_all, open_all
from group_publisher import VariableGroupPublisher
from mqtt_client import MQTTClient, MQTTConfig
from routing import ensure_routes


def start_quit_listener(stop_event: threading.Event) -> None:
    def _wait_for_q():
        try:
            while True:
                user_input = input()
                if user_input.strip().lower() == 'q':
                    stop_event.set()
                    break
        except EOFError:
            # If stdin closes, stop as well
            stop_event.set()

    threading.Thread(target=_wait_for_q, daemon=True).start()


def wait_until_stopped(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        stop_event.wait(timeout=0.2)


def state_change_handler(plc_name: str, old_state, new_state) -> None:
    """Callback invoked when PLC state changes."""
    print(f"[STATE CHANGE] {plc_name}: {old_state} -> {new_state}")


def start_variable_polling(
    connections: List[PLCConnection],
    stop_event: threading.Event,
    variable_configs: List[VariablePollConfig],
) -> List[threading.Thread]:
    threads: List[threading.Thread] = []

    for config in variable_configs:
        conn = next((c for c in connections if c.config.name == config.plc_name), None)
        if not conn:
            print(f"{config.plc_name} not connected, variable {config.symbol} disabled.")
            continue

        def _poll_variable(
            c: PLCConnection = conn,
            symbol: str = config.symbol,
            interval: float = config.interval_seconds,
        ) -> None:
            while not stop_event.is_set():
                try:
                    value = c.poll_variable(symbol)
                    print(f"[{c.config.name} Variable] {symbol} = {value}")
                except Exception as exc:
                    print(f"Error reading {symbol} from {c.config.name}: {exc}")
                if stop_event.wait(interval):
                    break

        thread = threading.Thread(target=_poll_variable, daemon=True)
        thread.start()
        threads.append(thread)

    return threads


def main() -> None:
    settings = load_settings()
    ensure_routes(settings)

    stop_event = threading.Event()
    connections = open_all(settings.plcs)
    if not connections:
        print('No active connections. Exiting.')
        return

    connections_by_name = {c.config.name: c for c in connections}

    # Set state change callback for all connections
    for conn in connections:
        conn.set_state_change_callback(state_change_handler)
        conn.start_state_polling()

    mqtt_client = None
    group_publishers: List[VariableGroupPublisher] = []
    if settings.mqtt.enabled:
        mqtt_client = MQTTClient(
            MQTTConfig(
                host=settings.mqtt.host,
                port=settings.mqtt.port,
                username=settings.mqtt.username,
                password=settings.mqtt.password,
                client_id=settings.mqtt.client_id,
            )
        )
        try:
            mqtt_client.connect()
        except Exception as exc:
            print(f"MQTT connect failed ({settings.mqtt.host}:{settings.mqtt.port}): {exc}")
            mqtt_client = None

    # Prefer grouped publishing when groups are defined
    variable_threads: List[threading.Thread] = []
    if mqtt_client and settings.variable_groups:
        for group in settings.variable_groups:
            publisher = VariableGroupPublisher(group, connections_by_name, mqtt_client, stop_event)
            publisher.start()
            group_publishers.append(publisher)
    else:
        # Backward compatible mode: print variable values to stdout
        variable_threads = start_variable_polling(connections, stop_event, settings.variable_polls)

    print("Press 'q' + Enter to quit.")
    start_quit_listener(stop_event)

    try:
        wait_until_stopped(stop_event)
    finally:
        for publisher in group_publishers:
            publisher.join(timeout=1.0)
        for thread in variable_threads:
            thread.join(timeout=1.0)
        close_all(connections)
        if mqtt_client:
            mqtt_client.disconnect()


if __name__ == '__main__':
    main()
