"""Beckhoff ADS Multi-PLC integraatio Home Assistantille.

Tukee useita PLC-yhteyksiä samanaikaisesti. Jokainen PLC lisätään
omana config entry -merkintänä, ja sille luodaan oma DataUpdateCoordinator.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import pyads

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AMS_NET_ID,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_VARIABLES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Alusta yksi PLC-yhteys config entrystä."""
    hass.data.setdefault(DOMAIN, {})

    plc_name = entry.data[CONF_PLC_NAME]
    ams_net_id = entry.data[CONF_AMS_NET_ID]
    ip_address = entry.data[CONF_IP_ADDRESS]
    ip_port = entry.data.get(CONF_IP_PORT, 851)
    # Muuttujat voivat olla joko options- tai data-kentässä riippuen
    # siitä onko ne lisätty config flow'ssa vai options flow'ssa
    variables = entry.options.get(CONF_VARIABLES) or entry.data.get(CONF_VARIABLES, [])

    # Luo ADS-yhteys (pyads) ajasäikeisesti
    try:
        plc = await hass.async_add_executor_job(
            _create_plc_connection, ams_net_id, ip_address, ip_port
        )
    except pyads.ADSError as err:
        raise ConfigEntryNotReady(
            f"Ei saada yhteyttä PLC:hen '{plc_name}' ({ams_net_id}): {err}"
        ) from err

    # Luo koordinaattori tälle PLC:lle
    coordinator = AdsPlcCoordinator(
        hass=hass,
        plc=plc,
        plc_name=plc_name,
        ams_net_id=ams_net_id,
        variables=variables,
        update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    )

    # Ensimmäinen päivitys – heittää ConfigEntryNotReady jos epäonnistuu
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "plc": plc,
        "plc_name": plc_name,
        "ams_net_id": ams_net_id,
        "variables": variables,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Lataa integraatio uudelleen automaattisesti kun options muuttuvat
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lataa entry uudelleen kun asetukset muuttuvat."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Sulje PLC-yhteys ja poista entiteetit."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        plc: pyads.Connection = entry_data["plc"]
        await hass.async_add_executor_job(plc.close)

    return unload_ok


def _create_plc_connection(
    ams_net_id: str, ip_address: str, ip_port: int
) -> pyads.Connection:
    """Luo ja avaa ADS-yhteys (synkroninen, ajetaan executor-säikeessä)."""
    plc = pyads.Connection(ams_net_id, ip_port, ip_address)
    plc.open()
    return plc


class AdsPlcCoordinator(DataUpdateCoordinator):
    """Koordinaattori yhdelle Beckhoff PLC:lle.

    Lukee kaikki määritetyt muuttujat kerralla ja välimuistittaa arvot.
    Entiteetit (sensor, switch jne.) hakevat arvonsa tästä koordinaattorista.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        plc: pyads.Connection,
        plc_name: str,
        ams_net_id: str,
        variables: list[dict[str, Any]],
        update_interval: timedelta,
    ) -> None:
        """Alusta koordinaattori."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"ADS PLC {plc_name}",
            update_interval=update_interval,
        )
        self.plc = plc
        self.plc_name = plc_name
        self.ams_net_id = ams_net_id
        self.variables = variables  # lista: [{name, type, friendly_name, ...}, ...]

    async def _async_update_data(self) -> dict[str, Any]:
        """Lue kaikki muuttujat PLC:ltä. Kutsutaan automaattisesti koordinaattorin toimesta."""
        try:
            return await self.hass.async_add_executor_job(self._read_all_variables)
        except pyads.ADSError as err:
            raise UpdateFailed(
                f"PLC '{self.plc_name}' lukuvirhe: {err}"
            ) from err

    def _read_all_variables(self) -> dict[str, Any]:
        """Synkroninen muuttujien luku (ajetaan executor-säikeessä)."""
        data: dict[str, Any] = {}
        for var in self.variables:
            var_name: str = var["name"]
            var_type: str = var["type"]
            try:
                value = self.plc.read_by_name(var_name, self._ads_type(var_type))
                data[var_name] = value
            except pyads.ADSError as err:
                _LOGGER.warning(
                    "PLC '%s': muuttujaa '%s' ei voitu lukea: %s",
                    self.plc_name,
                    var_name,
                    err,
                )
                # Säilytetään edellinen arvo jos mahdollista
                if self.data and var_name in self.data:
                    data[var_name] = self.data[var_name]
                else:
                    data[var_name] = None
        return data

    def write_variable(self, var_name: str, var_type: str, value: Any) -> None:
        """Kirjoita arvo PLC:lle (kutsuttava async_add_executor_job kautta)."""
        self.plc.write_by_name(var_name, value, self._ads_type(var_type))

    @staticmethod
    def _ads_type(type_str: str):
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
