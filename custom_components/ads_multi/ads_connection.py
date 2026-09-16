"""ADS-yhteyden ja route-lisäyksen apufunktiot."""

from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any

import pyads

from .const import (
    CONF_ENABLE_ROUTE,
    CONF_ROUTE_NAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_USERNAME,
    CONF_SENDER_AMS,
)

_LOGGER = logging.getLogger(__name__)


def get_pyads_version() -> str:
    """Palauta asennetun pyads-kirjaston versio."""
    module_version = getattr(pyads, "__version__", None)
    if module_version:
        return str(module_version)
    try:
        return package_version("pyads")
    except PackageNotFoundError:
        return "unknown"


def ads_type(type_str: str) -> type:
    """Muunna tyyppimerkkijono pyads-vakioksi."""
    type_map = {
        "BOOL": pyads.PLCTYPE_BOOL,
        "BYTE": pyads.PLCTYPE_BYTE,
        "WORD": pyads.PLCTYPE_WORD,
        "DWORD": pyads.PLCTYPE_DWORD,
        "INT": pyads.PLCTYPE_INT,
        "DINT": pyads.PLCTYPE_DINT,
        "REAL": pyads.PLCTYPE_REAL,
        "LREAL": pyads.PLCTYPE_LREAL,
        "STRING": pyads.PLCTYPE_STRING,
    }
    return type_map.get(type_str.upper(), pyads.PLCTYPE_REAL)


def add_ads_route(
    ams_net_id: str,
    ip_address: str,
    route_config: dict[str, Any],
) -> None:
    """Lisää ADS-route PLC:lle synkronisesti."""
    sender_ams = route_config[CONF_SENDER_AMS]
    route_name = route_config.get(CONF_ROUTE_NAME) or f"HA-{ams_net_id}"
    username = route_config.get(CONF_ROUTE_USERNAME) or ""
    password = route_config.get(CONF_ROUTE_PASSWORD) or ""

    _LOGGER.debug(
        "Route parametrit: sender_ams=%s, target_ams=%s, route_name=%s, username=%s",
        sender_ams,
        ams_net_id,
        route_name,
        username,
    )
    pyads.open_port()
    try:
        pyads.set_local_address(sender_ams)
        _LOGGER.info(
            "Lisätään route: sender=%s, target=%s, ip=%s",
            sender_ams,
            ams_net_id,
            ip_address,
        )
        pyads.add_route_to_plc(
            sender_ams,
            ip_address,
            ip_address,
            username,
            password,
            route_name=route_name,
        )
        _LOGGER.info(
            "Reitti '%s' lisätty onnistuneesti: %s -> %s",
            route_name,
            sender_ams,
            ams_net_id,
        )
    finally:
        pyads.close_port()


def create_plc_connection(
    ams_net_id: str,
    ip_address: str,
    ip_port: int,
    route_config: dict[str, Any] | None = None,
) -> pyads.Connection:
    """Luo ja avaa ADS-yhteys, optionaalisesti lisäämällä reitin."""
    _LOGGER.debug(
        "create_plc_connection aloitettu: ams_net_id=%s, ip=%s:%s",
        ams_net_id,
        ip_address,
        ip_port,
    )

    if route_config and route_config.get(CONF_ENABLE_ROUTE):
        _LOGGER.info("Route-lisääminen aktivoitu")
        try:
            add_ads_route(ams_net_id, ip_address, route_config)
        except pyads.ADSError as err:
            _LOGGER.warning(
                "Route-lisääminen epäonnistui (%s): %s. "
                "Yritetään muodostaa yhteys silti.",
                ams_net_id,
                err,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Route-lisääminen epäonnistui odottamattomalla virheellä (%s)",
                ams_net_id,
            )
    else:
        _LOGGER.debug(
            "Route-lisääminen ei aktivoitu (enable_route=%s)",
            route_config.get(CONF_ENABLE_ROUTE) if route_config else None,
        )

    _LOGGER.debug("Avataan PLC-yhteys: %s (%s:%s)", ams_net_id, ip_address, ip_port)
    plc = pyads.Connection(ams_net_id, ip_port, ip_address)
    plc.open()
    _LOGGER.info("PLC-yhteys avattu onnistuneesti: %s", ams_net_id)
    return plc


# Connection probe, not a pytest test.
def test_connection(
    ams_net_id: str,
    ip_address: str,
    ip_port: int,
    route_config: dict[str, Any] | None = None,  # noqa: PT028 - Runtime connection probe.
) -> bool:
    """Testaa ADS-yhteys synkronisesti ja lisää reitin tarvittaessa."""
    _LOGGER.debug(
        "test_connection aloitettu: ams_net_id=%s, ip=%s:%s",
        ams_net_id,
        ip_address,
        ip_port,
    )

    if route_config and route_config.get(CONF_ENABLE_ROUTE):
        _LOGGER.info("Route-lisääminen aktivoitu testissä")
        try:
            add_ads_route(ams_net_id, ip_address, route_config)
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Route-lisääminen epäonnistui testissä",
            )
            return False

    plc: pyads.Connection | None = None
    try:
        _LOGGER.debug("Avataan yhteys testille: %s", ams_net_id)
        plc = pyads.Connection(ams_net_id, ip_port, ip_address)
        plc.open()
        plc.read_state()
        _LOGGER.info("Yhteyden testaus onnistui: %s", ams_net_id)
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception(
            "ADS-yhteystesti epäonnistui (%s / %s)",
            ams_net_id,
            ip_address,
        )
        return False
    else:
        return True
    finally:
        if plc is not None:
            try:
                plc.close()
            except Exception:  # noqa: BLE001 - Cleanup must not hide the probe result.
                _LOGGER.debug("Testiyhteyden sulkeminen epäonnistui", exc_info=True)
