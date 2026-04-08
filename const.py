"""Vakiot Beckhoff ADS Multi-PLC integraatiolle."""

DOMAIN = "ads_multi"

# Config entry -avaimet
CONF_AMS_NET_ID = "ams_net_id"
CONF_IP_ADDRESS = "ip_address"
CONF_IP_PORT = "ip_port"
CONF_PLC_NAME = "plc_name"
CONF_VARIABLES = "variables"

# Route-konfiguraatio
CONF_ENABLE_ROUTE = "enable_route_config"
CONF_SENDER_AMS = "sender_ams"
CONF_ROUTE_USERNAME = "route_username"
CONF_ROUTE_PASSWORD = "route_password"
CONF_ROUTE_NAME = "route_name"

# Oletusarvot
DEFAULT_PORT = 851
DEFAULT_UPDATE_INTERVAL = 30  # sekuntia

# Entiteettityypit joita tuetaan
PLATFORMS = ["sensor", "binary_sensor", "switch", "number"]

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
