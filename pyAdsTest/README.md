# pyAdsTest - TwinCAT PLC Monitoring

A modular Python application for monitoring multiple Beckhoff TwinCAT PLCs using the pyads library. The application provides real-time state monitoring with change detection callbacks and configurable variable polling.

## Features

- **Multi-PLC Support**: Connect to and monitor multiple PLCs simultaneously
- **State Monitoring**: Automatic PLC state polling with configurable intervals (default: 1 second)
- **State Change Detection**: Callback notifications when PLC state changes
- **Variable Polling**: Poll specific PLC variables at configurable intervals
- **Thread-Safe**: Built-in locking for concurrent read operations
- **Graceful Shutdown**: Clean connection closure on exit (press 'q' to quit)
- **Modular Architecture**: Separated concerns across multiple modules

## Project Structure

```
pyAdsTest/
├── app.py              # Main application entry point
├── config.py           # PLC configurations and credentials
├── connections.py      # PLC connection management and state polling
├── routing.py          # ADS route setup and management
├── pollers.py          # (Optional) Additional polling utilities
└── README.md           # This file
```

## Prerequisites

- Python 3.7 or higher
- Conda or virtualenv (recommended)
- pyads library
- Beckhoff TwinCAT PLCs on the same network
- Network access to PLC ADS ports

## Installation

### 1. Create and Activate Virtual Environment

#### Using Conda (Recommended):
```bash
# Create environment
conda create -n pyads python=3.9

# Activate environment
conda activate pyads
```

#### Using venv:
```bash
# Create environment
python -m venv venv

# Activate environment (macOS/Linux)
source venv/bin/activate

# Activate environment (Windows)
venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

This project reads all configuration from a `.env` file.

1) Create your `.env` from the template:

```bash
cp .env.example .env
```

2) Edit `.env` to match your network and PLCs.

### PLC Connection Settings (.env)

The `.env.example` shows the full set of required variables:

- `PLC_USERNAME`, `PLC_PASSWORD`
- `SENDER_AMS`
- `ROUTE_NAME`
- `PLC_CONFIG_FILE` (path to the PLC config YAML, default `plc_config.yaml`)

The PLC config file is a YAML mapping with `plcs` and `variable_polls` lists. See `plc_config.yaml` for a full example.

Parameters:
- `plc_name`: Name of the PLC (must match PLCS config)
- `symbol`: TwinCAT variable path
- `interval_seconds`: Polling interval in seconds

When using the grouped format (`variables: [...]`), each variable may optionally define its own `plc_name` to read from a different PLC while still publishing everything to the same MQTT `topic`.

Grouped format options:
- `allow_partial` (bool, default false): publish `null` for failed reads instead of skipping the whole publish cycle.
- `retain` (bool, default false): publish the MQTT topic as retained.

## Usage

### Running the Application

```bash
# Make sure virtual environment is activated
conda activate pyads

# Run the application
python app.py
```

### Command Line Interface

Once running:
- The application will display PLC states and variable values in real-time
- State changes are highlighted with `[STATE CHANGE]` prefix
- Variable values are shown with `[<PLC_NAME> Variable]` prefix
- Press `q` + Enter to quit gracefully

### Example Output

```
Route added to PLC1 (192.168.0.10)
Route added to PLC2 (192.168.0.11)
Route added to PLC3 (192.168.0.12)
Local AMS: 192.168.0.92.1.1
Connected to PLC1 (192.168.0.10)
Connected to PLC2 (192.168.0.11)
Connected to PLC3 (192.168.0.12)
Press 'q' + Enter to quit.
[STATE CHANGE] PLC1: None -> (AdsState.RUN, 0)
[STATE CHANGE] PLC2: None -> (AdsState.RUN, 0)
[STATE CHANGE] PLC3: None -> (AdsState.RUN, 0)
[PLC3 Variable] HEATING.fbSensor1.OUT = 23.45
[PLC3 Variable] HEATING.fbPiController1.OUT = 67.89
```

## API Documentation

### PLCConnection Class

Main class for managing individual PLC connections.

#### Constructor
```python
PLCConnection(config: PLCConfig, state_poll_interval: float = 1.0)
```

#### Methods

**Connection Management:**
- `open()` - Open ADS connection to PLC
- `close()` - Close ADS connection (automatically stops polling)

**Data Access:**
- `poll_state()` - Read current PLC state (returns ADS state tuple)
- `poll_variable(symbol: str)` - Read a variable by name

**State Polling:**
- `start_state_polling()` - Start background state monitoring
- `stop_state_polling()` - Stop background state monitoring
- `set_state_poll_interval(interval_seconds: float)` - Update polling interval

**Callbacks:**
- `set_state_change_callback(callback)` - Set state change notification handler
  - Callback signature: `callback(plc_name: str, old_state, new_state)`

#### Example Usage

```python
from config import PLCConfig
from connections import PLCConnection

# Create connection with custom interval
config = PLCConfig('MyPLC', '192.168.0.10', '192.168.0.10.1.1', '192.168.0.10')
plc = PLCConnection(config, state_poll_interval=2.0)

# Open connection
plc.open()

# Set up state change callback
def on_state_change(name, old, new):
    print(f"{name} changed from {old} to {new}")

plc.set_state_change_callback(on_state_change)
plc.start_state_polling()

# Poll a variable
value = plc.poll_variable("MAIN.Temperature")
print(f"Temperature: {value}")

# Cleanup
plc.close()
```

### Helper Functions

**routing.py:**
- `ensure_routes(configs: List[PLCConfig])` - Set up ADS routes to PLCs

**connections.py:**
- `open_all(configs: List[PLCConfig])` - Open connections to all PLCs
- `close_all(connections: List[PLCConnection])` - Close all connections

## Troubleshooting

### Connection Issues

**Problem**: Cannot connect to PLC
- Verify PLC IP address and AMS NetID are correct
- Ensure TwinCAT is running on the PLC
- Check network connectivity (`ping <plc_ip>`)
- Verify ADS route exists on PLC (use TwinCAT System Manager)

**Problem**: Route warnings
- Routes may already exist - these warnings are usually safe to ignore
- To reset routes, delete them in TwinCAT System Manager

### State Polling Issues

**Problem**: State changes not detected
- Verify callback function signature matches: `(plc_name: str, old_state, new_state)`
- Check that `start_state_polling()` was called after setting callback
- First state read will show `old_state=None`

### Variable Polling Issues

**Problem**: Variable not found
- Verify variable path is correct (case-sensitive)
- Check that variable is published in TwinCAT
- Ensure variable is in the correct PLC runtime

## Development

### Adding New PLCs

1. Edit `config.py`
2. Add new `PLCConfig` to `PLCS` list
3. Update `SENDER_AMS` if needed

### Adding New Variables to Monitor

1. Edit `config.py`
2. Add new `VariablePollConfig` to `VARIABLE_POLLS` list
3. Specify PLC name, variable path, and interval

### Customizing State Change Handler

Edit `state_change_handler` in `app.py`:

```python
def state_change_handler(plc_name: str, old_state, new_state) -> None:
    # Custom logic here
    # Example: Log to file, send notification, etc.
    print(f"[STATE CHANGE] {plc_name}: {old_state} -> {new_state}")
```

## Dependencies

- **pyads**: Python wrapper for TwinCAT ADS library
  - Installation: `pip install pyads`
  - Documentation: https://pyads.readthedocs.io/

## License

This project is provided as-is for educational and development purposes.

## Support

For issues related to:
- **pyads library**: See https://github.com/stlehmann/pyads
- **TwinCAT ADS**: See Beckhoff documentation
- **This project**: Check connection settings in `config.py`

## Version History

- **Current**: Multi-PLC monitoring with state change detection and variable polling
- Modular architecture with separated concerns
- Thread-safe operations with configurable intervals
