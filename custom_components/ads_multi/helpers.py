"""Yhteiset apufunktiot ADS Multi -integraatiolle."""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING, Any

from .const import CONF_ASYNC_READ, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN_STATE_KEY = "__domain_state"
SERVICES_REGISTERED_KEY = "services_registered"
AUTO_EXPORT_GUARD_KEY = "auto_export_in_progress"


def domain_state(hass: HomeAssistant) -> dict[str, Any]:
    """Palauta domainin sisäinen tila-avaruus."""
    hass.data.setdefault(DOMAIN, {})
    state = hass.data[DOMAIN].get(DOMAIN_STATE_KEY)
    if isinstance(state, dict):
        return state
    state = {
        SERVICES_REGISTERED_KEY: False,
        AUTO_EXPORT_GUARD_KEY: False,
    }
    hass.data[DOMAIN][DOMAIN_STATE_KEY] = state
    return state


def effective_option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Palauta config entryn arvo prioriteetilla options > data."""
    if key in entry.options:
        return entry.options.get(key)
    return entry.data.get(key, default)


def normalize_variables(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalisoi muuttujalista ja lisää puuttuvat oletusarvot."""
    normalized: list[dict[str, Any]] = []
    for variable in variables:
        item = dict(variable)
        item[CONF_ASYNC_READ] = bool(item.get(CONF_ASYNC_READ, False))
        normalized.append(item)
    return normalized


def resolve_local_ip(target_ip: str) -> str:
    """Päättele paikallinen lähde-IP, jota käytettäisiin kohde-IP:lle."""
    local_ip = target_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target_ip, 1))
            local_ip = sock.getsockname()[0]
    except OSError as err:
        _LOGGER.warning(
            "Paikallisen IP-osoitteen päätteleminen epäonnistui kohteelle %s: %s. "
            "Käytetään fallbackia.",
            target_ip,
            err,
        )
    return local_ip


def sender_ams_from_ip(local_ip: str) -> str:
    """Muodosta lähettäjän AMS Net ID annetusta IP-osoitteesta."""
    return f"{local_ip}.1.1"


def resolve_sender_ams(target_ip: str) -> str:
    """Muodosta lähettäjän AMS Net ID paikallisen lähde-IP:n perusteella."""
    return sender_ams_from_ip(resolve_local_ip(target_ip))
