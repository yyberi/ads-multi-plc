import os
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv
import yaml


@dataclass(frozen=True)
class PLCConfig:
    name: str
    ip: str
    ams: str
    host: str


@dataclass(frozen=True)
class VariablePollConfig:
    plc_name: str
    symbol: str
    interval_seconds: float


@dataclass(frozen=True)
class VariableConfig:
    name: str
    symbol: str
    plc_name: Optional[str] = None


@dataclass(frozen=True)
class VariableGroupConfig:
    plc_name: Optional[str]
    topic: str
    name: str
    interval_seconds: float
    variables: List[VariableConfig]
    allow_partial: bool = False
    retain: bool = False


@dataclass(frozen=True)
class MQTTSettings:
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    client_id: str
    enabled: bool


@dataclass(frozen=True)
class Settings:
    plc_username: str
    plc_password: str
    sender_ams: str
    route_name: str
    plcs: List[PLCConfig]
    variable_polls: List[VariablePollConfig]
    variable_groups: List[VariableGroupConfig]
    mqtt: MQTTSettings


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _env_int(name: str, default: Optional[int] = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        if default is None:
            raise ValueError(f"Missing required environment variable: {name}")
        return default
    return int(raw)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == '':
        raise ValueError(
            f"Missing required environment variable: {name}. "
            f"Create a .env file (see .env.example)."
        )
    return value


def _resolve_config_path(config_path: str, env_file: Optional[str]) -> str:
    expanded = os.path.expanduser(config_path)
    if os.path.isabs(expanded):
        return expanded
    base_dir = os.path.dirname(os.path.abspath(env_file)) if env_file else os.getcwd()
    return os.path.join(base_dir, expanded)


def _load_yaml_file(path: str):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ValueError(
            f"PLC config file not found: {path}. "
            "Set PLC_CONFIG_FILE or create the file."
        ) from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in PLC config file {path}: {exc}") from exc


def _normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def load_settings(env_file: Optional[str] = None) -> Settings:
    """Load runtime settings from `.env` (or a custom env_file).

    This function has no side effects until called (so importing this module
    won't fail if `.env` is missing).
    """

    load_dotenv(dotenv_path=env_file, override=False)

    plc_username = _require_env('PLC_USERNAME')
    plc_password = _require_env('PLC_PASSWORD')
    sender_ams = _require_env('SENDER_AMS')
    route_name = _require_env('ROUTE_NAME')

    mqtt_enabled = _env_bool('MQTT_ENABLED', default=True)
    mqtt_host = os.getenv('MQTT_HOST', 'localhost')
    mqtt_port = _env_int('MQTT_PORT', default=1883)
    mqtt_username = os.getenv('MQTT_USERNAME')
    mqtt_password = os.getenv('MQTT_PASSWORD')
    mqtt_client_id = os.getenv('MQTT_CLIENT_ID', 'pyAdsTest')

    plc_config_file = os.getenv('PLC_CONFIG_FILE', 'plc_config.yaml').strip() or 'plc_config.yaml'
    plc_config_path = _resolve_config_path(plc_config_file, env_file)
    plc_config = _load_yaml_file(plc_config_path)
    if not isinstance(plc_config, dict):
        raise ValueError('PLC config file must be a YAML mapping')

    plcs_raw = plc_config.get('plcs')
    if plcs_raw is None:
        raise ValueError("PLC config file missing 'plcs' list")
    if not isinstance(plcs_raw, list):
        raise ValueError("PLC config file 'plcs' must be a YAML list")
    plcs = [
        PLCConfig(
            name=item['name'],
            ip=item['ip'],
            ams=item['ams'],
            host=item.get('host', item['ip']),
        )
        for item in plcs_raw
    ]

    polls_raw = plc_config.get('variable_polls')
    if polls_raw is None:
        raise ValueError("PLC config file missing 'variable_polls' list")
    if not isinstance(polls_raw, list):
        raise ValueError("PLC config file 'variable_polls' must be a YAML list")

    # Backward compatibility:
    # - old format: [{plc_name, symbol, interval_seconds}, ...]
    # - new format: [{plc_name, topic, name, interval_seconds, variables:[{name,symbol},...]}, ...]
    variable_polls: List[VariablePollConfig] = []
    variable_groups: List[VariableGroupConfig] = []

    if len(polls_raw) > 0 and isinstance(polls_raw[0], dict) and 'variables' in polls_raw[0]:
        for group in polls_raw:
            variables_raw = group.get('variables', [])
            if not isinstance(variables_raw, list):
                raise ValueError("PLC config file 'variable_polls' group.variables must be a YAML list")
            variables = [
                VariableConfig(
                    name=v['name'],
                    symbol=v['symbol'],
                    plc_name=_normalize_optional_str(v.get('plc_name')),
                )
                for v in variables_raw
            ]
            group_plc_name = _normalize_optional_str(group.get('plc_name'))

            if group_plc_name is None and any(v.plc_name is None for v in variables):
                raise ValueError(
                    "PLC config file 'variable_polls' group must define plc_name or every variable must define plc_name"
                )
            variable_groups.append(
                VariableGroupConfig(
                    plc_name=group_plc_name,
                    topic=group['topic'],
                    name=group.get('name', ''),
                    interval_seconds=float(group.get('interval_seconds', 10.0)),
                    variables=variables,
                    allow_partial=bool(group.get('allow_partial', False)),
                    retain=bool(group.get('retain', False)),
                )
            )
    else:
        # keep supporting the old flat list
        variable_polls = [
            VariablePollConfig(
                plc_name=item['plc_name'],
                symbol=item['symbol'],
                interval_seconds=float(item['interval_seconds']),
            )
            for item in polls_raw
        ]

    return Settings(
        plc_username=plc_username,
        plc_password=plc_password,
        sender_ams=sender_ams,
        route_name=route_name,
        plcs=plcs,
        variable_polls=variable_polls,
        variable_groups=variable_groups,
        mqtt=MQTTSettings(
            host=mqtt_host,
            port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            client_id=mqtt_client_id,
            enabled=mqtt_enabled,
        ),
    )
