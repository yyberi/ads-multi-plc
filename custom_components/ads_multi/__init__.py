"""Beckhoff ADS Multi-PLC integraatio Home Assistantille."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import pyads

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .ads_connection import create_plc_connection, get_pyads_version
from .const import (
    CONF_AMS_NET_ID,
    CONF_DEVICE_PROFILES,
    CONF_ENABLE_ROUTE,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_ROUTE_NAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_USERNAME,
    CONF_SENDER_AMS,
    CONF_VARIABLES,
    DEFAULT_PORT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import AdsPlcCoordinator
from .entity_profiles import ensure_profile_ids
from .helpers import (
    AUTO_EXPORT_GUARD_KEY,
    domain_state,
    effective_option,
    normalize_variables,
    resolve_local_ip,
    sender_ams_from_ip,
)
from .settings import async_export_settings_to_yaml, async_register_settings_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Alusta integraation domain-tason palvelut."""
    domain_state(hass)
    await async_register_settings_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Alusta yksi PLC-yhteys config entrystä."""
    hass.data.setdefault(DOMAIN, {})
    await async_register_settings_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    plc_name = entry.data[CONF_PLC_NAME]
    ams_net_id = entry.data[CONF_AMS_NET_ID]
    ip_address = entry.data[CONF_IP_ADDRESS]
    ip_port = entry.data.get(CONF_IP_PORT, DEFAULT_PORT)
    local_ip = resolve_local_ip(ip_address)
    sender_ams = (
        effective_option(entry, CONF_SENDER_AMS, "")
        or sender_ams_from_ip(local_ip)
    )
    variables = normalize_variables(effective_option(entry, CONF_VARIABLES, []) or [])
    device_profiles = ensure_profile_ids(
        effective_option(entry, CONF_DEVICE_PROFILES, []) or []
    )
    route_config = {
        CONF_ENABLE_ROUTE: effective_option(entry, CONF_ENABLE_ROUTE, False),
        CONF_SENDER_AMS: sender_ams,
        CONF_ROUTE_NAME: effective_option(entry, CONF_ROUTE_NAME, ""),
        CONF_ROUTE_USERNAME: effective_option(entry, CONF_ROUTE_USERNAME, ""),
        CONF_ROUTE_PASSWORD: effective_option(entry, CONF_ROUTE_PASSWORD, ""),
    }

    try:
        plc = await hass.async_add_executor_job(
            create_plc_connection,
            ams_net_id,
            ip_address,
            ip_port,
            route_config,
        )
    except pyads.ADSError as err:
        raise ConfigEntryNotReady(
            f"Ei saada yhteyttä PLC:hen '{plc_name}' ({ams_net_id}): {err}"
        ) from err

    coordinator = AdsPlcCoordinator(
        hass=hass,
        plc=plc,
        plc_name=plc_name,
        ams_net_id=ams_net_id,
        variables=variables,
        device_profiles=device_profiles,
        update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    )

    try:
        await coordinator.async_setup_notifications()
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_release_notifications()
        await hass.async_add_executor_job(plc.close)
        raise

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "plc": plc,
        "plc_name": plc_name,
        "ams_net_id": ams_net_id,
        "variables": variables,
        "device_profiles": device_profiles,
        "ip_address": ip_address,
        "ip_port": ip_port,
        "current_ip_address": local_ip,
        "sender_ams": sender_ams,
        "pyads_version": get_pyads_version(),
    }

    state = domain_state(hass)
    if not state.get(AUTO_EXPORT_GUARD_KEY, False):
        try:
            await async_export_settings_to_yaml(hass)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Asetusten automaattinen YAML-vienti epäonnistui: %s", err)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lataa entry uudelleen kun asetukset muuttuvat."""
    state = domain_state(hass)
    if not state.get(AUTO_EXPORT_GUARD_KEY, False):
        try:
            await async_export_settings_to_yaml(hass)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Asetusten automaattinen YAML-vienti epäonnistui: %s", err)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Sulje PLC-yhteys ja poista entiteetit."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator: AdsPlcCoordinator = entry_data["coordinator"]
        plc: pyads.Connection = entry_data["plc"]
        await coordinator.async_release_notifications()
        await hass.async_add_executor_job(plc.close)

    return unload_ok
