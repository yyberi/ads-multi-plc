"""Beckhoff ADS Multi-PLC integraatio Home Assistantille.

Tukee useita PLC-yhteyksiä samanaikaisesti. Jokainen PLC lisätään
omana config entry -merkintänä, ja sille luodaan oma DataUpdateCoordinator.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
import logging
import socket
from datetime import timedelta
from typing import Any

import pyads

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AMS_NET_ID,
    CONF_DEVICE_PROFILES,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_VARIABLES,
    CONF_ENABLE_ROUTE,
    CONF_SENDER_AMS,
    CONF_ROUTE_USERNAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_NAME,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .entity_profiles import collect_profile_points, normalize_profiles

_LOGGER = logging.getLogger(__name__)


def _resolve_local_ip(target_ip: str) -> str:
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


def _sender_ams_from_ip(local_ip: str) -> str:
    """Muodosta lähettäjän AMS Net ID annetusta IP-osoitteesta."""
    return f"{local_ip}.1.1"


def _get_pyads_version() -> str:
    """Palauta asennetun pyads-kirjaston versio."""
    module_version = getattr(pyads, "__version__", None)
    if module_version:
        return str(module_version)
    try:
        return package_version("pyads")
    except PackageNotFoundError:
        return "unknown"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Alusta yksi PLC-yhteys config entrystä."""
    hass.data.setdefault(DOMAIN, {})

    # Rekisteröi reload-listener heti – ennen mahdollisia virheitä
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    plc_name = entry.data[CONF_PLC_NAME]
    ams_net_id = entry.data[CONF_AMS_NET_ID]
    ip_address = entry.data[CONF_IP_ADDRESS]
    ip_port = entry.data.get(CONF_IP_PORT, 851)
    local_ip = _resolve_local_ip(ip_address)
    sender_ams = (
        entry.options.get(CONF_SENDER_AMS)
        or entry.data.get(CONF_SENDER_AMS)
        or _sender_ams_from_ip(local_ip)
    )
    # Muuttujat voivat olla joko options- tai data-kentässä riippuen
    # siitä onko ne lisätty config flow'ssa vai options flow'ssa
    variables = entry.options.get(CONF_VARIABLES) or entry.data.get(CONF_VARIABLES, [])
    device_profiles = normalize_profiles(
        entry.options.get(CONF_DEVICE_PROFILES, entry.data.get(CONF_DEVICE_PROFILES, []))
    )

    # Kerää route-konfiguraatio (prioriteetti: options > data)
    route_config = {
        CONF_ENABLE_ROUTE: entry.options.get(
            CONF_ENABLE_ROUTE, entry.data.get(CONF_ENABLE_ROUTE, False)
        ),
        CONF_SENDER_AMS: sender_ams,
        CONF_ROUTE_NAME: entry.options.get(CONF_ROUTE_NAME, entry.data.get(CONF_ROUTE_NAME, "")),
        CONF_ROUTE_USERNAME: entry.options.get(
            CONF_ROUTE_USERNAME, entry.data.get(CONF_ROUTE_USERNAME, "")
        ),
        CONF_ROUTE_PASSWORD: entry.options.get(
            CONF_ROUTE_PASSWORD, entry.data.get(CONF_ROUTE_PASSWORD, "")
        ),
    }

    # Luo ADS-yhteys (pyads) ajasäikeisesti
    try:
        plc = await hass.async_add_executor_job(
            _create_plc_connection, ams_net_id, ip_address, ip_port, route_config
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
        device_profiles=device_profiles,
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
        "device_profiles": device_profiles,
        "ip_address": ip_address,
        "ip_port": ip_port,
        "current_ip_address": local_ip,
        "sender_ams": sender_ams,
        "pyads_version": _get_pyads_version(),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
    ams_net_id: str,
    ip_address: str,
    ip_port: int,
    route_config: dict[str, Any] | None = None,
) -> pyads.Connection:
    """Luo ja avaa ADS-yhteys, optionaalisesti lisäämällä reitin."""
    _LOGGER.debug("_create_plc_connection aloitettu: ams_net_id=%s, ip=%s:%s",
                  ams_net_id, ip_address, ip_port)
    _LOGGER.debug("route_config: %s", route_config)

    # Lisää reitti jos konfiguroitu
    if route_config and route_config.get(CONF_ENABLE_ROUTE):
        _LOGGER.info("Route-lisääminen aktivoitu")
        try:
            sender_ams = route_config[CONF_SENDER_AMS]
            route_name = route_config.get(CONF_ROUTE_NAME) or f"HA-{ams_net_id}"
            username = route_config.get(CONF_ROUTE_USERNAME) or ""
            password = route_config.get(CONF_ROUTE_PASSWORD) or ""

            _LOGGER.debug("Route parametrit: sender_ams=%s, target_ams=%s, route_name=%s, username=%s",
                         sender_ams, ams_net_id, route_name, username)

            # Avaa portti route-lisäämistä varten
            _LOGGER.debug("Avataan pyads portti...")
            pyads.open_port()
            _LOGGER.debug("Portti avattu")

            try:
                _LOGGER.debug("Asetetaan local address: %s", sender_ams)
                pyads.set_local_address(sender_ams)
                _LOGGER.debug("Local address asetettu")

                # Käytä positioargumentteja kuten pyAdsTest:ssa
                _LOGGER.info("Lisätään route: sender=%s, target=%s, ip=%s, port=%s",
                            sender_ams, ams_net_id, ip_address, ip_port)
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
                    route_name, sender_ams, ams_net_id,
                )
            except Exception as err:
                _LOGGER.error("Virhe route-lisäyksessä: %s", err, exc_info=True)
                raise
            finally:
                _LOGGER.debug("Suljetaan pyads portti...")
                pyads.close_port()
                _LOGGER.debug("Portti suljettu")
        except pyads.ADSError as err:
            # Varoita, mutta älä estä yhteyttä
            _LOGGER.warning(
                "Route-lisääminen epäonnistui (%s): %s. "
                "Yritetään muodostaa yhteys silti.",
                ams_net_id, err,
            )
        except Exception as err:
            # Muut virheet
            _LOGGER.error(
                "Route-lisääminen epäonnistui odottamattomalla virheellä (%s): %s",
                ams_net_id, err, exc_info=True
            )
    else:
        _LOGGER.debug("Route-lisääminen ei aktivoitu (enable_route=%s)",
                     route_config.get(CONF_ENABLE_ROUTE) if route_config else None)

    # Avaa yhteys normaalisti
    _LOGGER.debug("Avataan PLC-yhteys: %s (%s:%s)", ams_net_id, ip_address, ip_port)
    plc = pyads.Connection(ams_net_id, ip_port, ip_address)
    plc.open()
    _LOGGER.info("PLC-yhteys avattu onnistuneesti: %s", ams_net_id)
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
        device_profiles: list[dict[str, Any]],
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
        self.device_profiles = device_profiles
        self.read_points = self._build_read_points(variables, device_profiles)

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
        for point in self.read_points:
            var_name: str = point["name"]
            var_type: str = point["type"]
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

    @staticmethod
    def _build_read_points(
        variables: list[dict[str, Any]],
        device_profiles: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Yhdistä luettavat pointit muuttujista ja profiileista."""
        points: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for var in variables:
            name = str(var.get("name", ""))
            var_type = str(var.get("type", "")).upper()
            if not name or not var_type:
                continue
            key = (name, var_type)
            if key in seen:
                continue
            seen.add(key)
            points.append({"name": name, "type": var_type})

        for point in collect_profile_points(device_profiles):
            key = (point["name"], point["type"])
            if key in seen:
                continue
            seen.add(key)
            points.append(point)

        return points

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
