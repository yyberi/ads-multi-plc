# ADS (Multi-PLC) - Home Assistant Custom Integration

This integration connects one or more Beckhoff TwinCAT PLCs to Home Assistant using ADS (`pyads`).
Each PLC is added as its own config entry and appears as a separate device with its own entities.

## Features

- Multi-PLC support (one config entry per PLC)
- Variable-based entities:
  - `sensor`
  - `binary_sensor`
  - `switch`
  - `number`
- Profile-based `light` entities (on/off, optional brightness, optional color temperature)
- Optional automatic ADS route setup during onboarding
- Hybrid read model:
  - Polling for regular reads
  - ADS notifications for async reads
- Diagnostics entities for:
  - `pyads` version
  - current local IP
  - async subscription count and status

## Requirements

- Home Assistant 2023.1+
- `pyads >= 3.3.9` (installed automatically via `manifest.json`)
- Network access to your PLC(s)
- ADS route configured, or credentials available for automatic route setup

## Installation

1. Copy `custom_components/ads_multi/` to:
   - `<config>/custom_components/ads_multi/`
2. Restart Home Assistant.
3. Go to **Settings -> Devices & Services -> Add Integration**.
4. Search for **ADS (Multi-PLC)**.
5. Add each PLC as a separate integration entry.

## Configuration Flow

### 1) Add PLC connection

During initial setup, provide:

- PLC name
- AMS Net ID (for example `192.168.1.100.1.1`)
- PLC IP address (for example `192.168.1.100`)
- ADS port (default `851`)
- Optional route settings:
  - enable automatic route creation
  - route name
  - PLC username/password for route creation

After connection test succeeds, the PLC entry is created.

### 2) Configure variables and lights (Options)

Open the integration entry and use **Configure** to manage:

- Add/remove variables
- Add/edit/remove light profiles
- Edit route configuration

## Variable Configuration

Supported variable types in UI:

- `BOOL`
- `BYTE`
- `WORD`
- `DWORD`
- `INT`
- `DINT`
- `REAL`
- `LREAL`
- `STRING`

Variable fields:

- `name` (required): full PLC symbol, e.g. `MAIN.rRoomTemp`
- `type` (required): ADS data type
- `friendly_name` (optional)
- `unit` (optional)
- `device_class` (optional)
- `writable` (optional):
  - writable `BOOL` -> `switch`
  - writable numeric types -> `number`
- `async_read` (optional):
  - enables ADS notification-based updates for that variable

## Light Profiles

Light entities are configured from profiles in Options:

- Required:
  - profile name
  - on/off symbol (`BOOL`)
- Optional:
  - brightness symbol + range
  - color temperature symbol + range

Light profile points are automatically treated as async-read points, so external PLC-side changes are pushed to Home Assistant immediately (when notifications are available).

## Read Modes

### Polling (synchronous)

- Used for regular variables
- Also used as fallback if notification subscription fails
- Poll interval is controlled by `DEFAULT_UPDATE_INTERVAL` in `const.py` (default 30 seconds)

### ADS notifications (asynchronous)

- Used when `async_read` is enabled for a variable
- Also used automatically for light profile points
- Subscriptions are created when the entry is set up and released on unload

## Entity Mapping

| PLC type | Writable | Home Assistant entity |
|---|---|---|
| `BOOL` | No | `binary_sensor` |
| `BOOL` | Yes | `switch` |
| `BYTE/WORD/DWORD/INT/DINT/REAL/LREAL` | No | `sensor` |
| `BYTE/WORD/DWORD/INT/DINT/REAL/LREAL` | Yes | `number` |
| `STRING` | No | `sensor` |
| Light profile | N/A | `light` |

## Diagnostics

The integration exposes diagnostic entities, including:

- `pyads` version
- current local IP address
- active async subscription count

Subscription diagnostics include configured, active, and failed async subscription counters.

## Logging / Troubleshooting

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ads_multi: debug
```

If async updates do not appear immediately:

- verify PLC route and connectivity
- check integration diagnostics for failed async subscriptions
- confirm symbol names/types match PLC definitions
