"""Vakiot Beckhoff ADS Multi-PLC integraatiolle."""

DOMAIN = "ads_multi"

# Config entry -avaimet
CONF_AMS_NET_ID = "ams_net_id"
CONF_IP_ADDRESS = "ip_address"
CONF_IP_PORT = "ip_port"
CONF_PLC_NAME = "plc_name"
CONF_VARIABLES = "variables"
CONF_ASYNC_READ = "async_read"
CONF_DEVICE_PROFILES = "device_profiles"

# Route-konfiguraatio
CONF_ENABLE_ROUTE = "enable_route_config"
CONF_SENDER_AMS = "sender_ams"
CONF_ROUTE_USERNAME = "route_username"
CONF_ROUTE_PASSWORD = "route_password"
CONF_ROUTE_NAME = "route_name"
CONF_ROUTE_PASSWORD_SECRET = "route_password_secret"

# Profiilipohjaiset laitteet
CONF_PROFILE_TYPE = "profile_type"
PROFILE_TYPE_LIGHT = "light"
PROFILE_KEY_ID = "id"
PROFILE_KEY_NAME = "name"
PROFILE_KEY_TYPE = "type"
PROFILE_KEY_MIN = "min"
PROFILE_KEY_MAX = "max"
LIGHT_KEY_ON_OFF = "on_off"
LIGHT_KEY_BRIGHTNESS = "brightness"
LIGHT_KEY_COLOR_TEMP = "color_temp"

# Oletusarvot
DEFAULT_PORT = 851
DEFAULT_UPDATE_INTERVAL = 30  # sekuntia

# Entiteettityypit joita tuetaan
PLATFORMS = ["sensor", "binary_sensor", "switch", "number", "light"]

# Asetusten varmuuskopiointi / palautus
DEFAULT_SETTINGS_FILE = f"{DOMAIN}_settings.yaml"
SERVICE_EXPORT_SETTINGS = "export_settings"
SERVICE_IMPORT_SETTINGS = "import_settings"
SERVICE_FIELD_FILE_PATH = "file_path"
SERVICE_FIELD_OVERWRITE_EXISTING = "overwrite_existing"

# ADS-muuttujatyypit → Python-tyypit
ADS_TYPEMAP = {
    "BOOL": bool,
    "BYTE": int,
    "WORD": int,
    "DWORD": int,
    "INT": int,
    "DINT": int,
    "REAL": float,
    "LREAL": float,
    "STRING": str,
    "TIME": int,
    "TOD": int,
    "DATE": int,
    "DT": int,
}

# Attribuuttiavaimet
ATTR_PLC_NAME = "plc_name"
ATTR_AMS_NET_ID = "ams_net_id"
ATTR_VAR_NAME = "variable_name"
ATTR_VAR_TYPE = "variable_type"
ATTR_PLC_IP_ADDRESS = "plc_ip_address"
ATTR_CURRENT_IP_ADDRESS = "current_ip_address"
ATTR_ADS_PORT = "ads_port"
ATTR_PYADS_VERSION = "pyads_version"
ATTR_ACTIVE_ASYNC_SUBSCRIPTIONS = "active_async_subscriptions"
ATTR_CONFIGURED_ASYNC_VARIABLES = "configured_async_variables"
ATTR_FAILED_ASYNC_SUBSCRIPTIONS = "failed_async_subscriptions"
