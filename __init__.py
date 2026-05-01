"""Beckhoff ADS Multi-PLC integraatio Home Assistantille.

Tukee useita PLC-yhteyksiä samanaikaisesti. Jokainen PLC lisätään
omana config entry -merkintänä, ja sille luodaan oma DataUpdateCoordinator.
"""
from __future__ import annotations

import ctypes
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version as package_version
import logging
from pathlib import Path
import re
import socket
from datetime import timedelta
from typing import Any

import pyads
import voluptuous as vol
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AMS_NET_ID,
    CONF_ASYNC_READ,
    CONF_DEVICE_PROFILES,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    PROFILE_TYPE_LIGHT,
    CONF_VARIABLES,
    CONF_ENABLE_ROUTE,
    CONF_SENDER_AMS,
    CONF_ROUTE_USERNAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_NAME,
    CONF_ROUTE_PASSWORD_SECRET,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_SETTINGS_FILE,
    DOMAIN,
    PLATFORMS,
    SERVICE_EXPORT_SETTINGS,
    SERVICE_IMPORT_SETTINGS,
    SERVICE_FIELD_FILE_PATH,
    SERVICE_FIELD_OVERWRITE_EXISTING,
)
from .entity_profiles import (
    collect_profile_points,
    ensure_profile_ids,
    get_profiles_by_type,
)

_LOGGER = logging.getLogger(__name__)

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_YAML_ENTRIES_KEY = "entries"
SETTINGS_YAML_VERSION_KEY = "version"
SETTINGS_YAML_EXPORTED_AT_KEY = "exported_at"
SETTINGS_YAML_PASSWORD_SECRET_KEY = CONF_ROUTE_PASSWORD_SECRET
DOMAIN_STATE_KEY = "__domain_state"
SERVICES_REGISTERED_KEY = "services_registered"
AUTO_EXPORT_GUARD_KEY = "auto_export_in_progress"


def _domain_state(hass: HomeAssistant) -> dict[str, Any]:
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


def _settings_file_path(hass: HomeAssistant, path_override: str | None = None) -> Path:
    """Palauta asetustiedoston polku."""
    if path_override:
        override_path = Path(path_override)
        if not override_path.is_absolute():
            return Path(hass.config.path(path_override))
        return override_path
    return Path(hass.config.path(DEFAULT_SETTINGS_FILE))


def _secrets_file_path(hass: HomeAssistant) -> Path:
    """Palauta Home Assistantin secrets.yaml polku."""
    return Path(hass.config.path("secrets.yaml"))


def _safe_read_yaml(path: Path) -> dict[str, Any]:
    """Lue YAML-tiedosto sanakirjaksi."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        content = yaml.safe_load(handle) or {}
    if not isinstance(content, dict):
        raise HomeAssistantError(f"YAML-tiedoston '{path}' juuressa pitää olla map-rakenne.")
    return content


def _safe_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Kirjoita YAML atomisesti."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
    temp_path.replace(path)


def _secret_key_for_ams(ams_net_id: str) -> str:
    """Muodosta vakioitu secret-avain PLC-kohtaiselle salasanalle."""
    suffix = re.sub(r"[^a-z0-9]+", "_", ams_net_id.lower()).strip("_")
    if not suffix:
        suffix = "plc"
    return f"{DOMAIN}_route_password_{suffix}"


def _effective_option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Palauta arvo prioriteetilla options > data."""
    if key in entry.options:
        return entry.options.get(key)
    return entry.data.get(key, default)


def _entry_to_export_payload(entry: ConfigEntry) -> dict[str, Any]:
    """Muunna config entry serialisoitavaan muotoon."""
    payload: dict[str, Any] = {
        CONF_PLC_NAME: entry.data.get(CONF_PLC_NAME, ""),
        CONF_AMS_NET_ID: entry.data.get(CONF_AMS_NET_ID, ""),
        CONF_IP_ADDRESS: entry.data.get(CONF_IP_ADDRESS, ""),
        CONF_IP_PORT: entry.data.get(CONF_IP_PORT, 851),
        CONF_VARIABLES: _normalize_variables(
            _effective_option(entry, CONF_VARIABLES, [])
        ),
        CONF_DEVICE_PROFILES: ensure_profile_ids(
            _effective_option(entry, CONF_DEVICE_PROFILES, [])
        ),
        CONF_ENABLE_ROUTE: bool(_effective_option(entry, CONF_ENABLE_ROUTE, False)),
        CONF_ROUTE_NAME: _effective_option(entry, CONF_ROUTE_NAME, ""),
        CONF_ROUTE_USERNAME: _effective_option(entry, CONF_ROUTE_USERNAME, ""),
        CONF_SENDER_AMS: _effective_option(entry, CONF_SENDER_AMS, ""),
    }
    secret_key = _effective_option(entry, CONF_ROUTE_PASSWORD_SECRET, "")
    if secret_key:
        payload[SETTINGS_YAML_PASSWORD_SECRET_KEY] = str(secret_key)
    return payload


def _build_entry_data_from_payload(payload: dict[str, Any], route_password: str) -> dict[str, Any]:
    """Rakenna config entryn data importoitavasta payloadista."""
    return {
        CONF_PLC_NAME: payload[CONF_PLC_NAME],
        CONF_AMS_NET_ID: payload[CONF_AMS_NET_ID],
        CONF_IP_ADDRESS: payload[CONF_IP_ADDRESS],
        CONF_IP_PORT: int(payload.get(CONF_IP_PORT, 851)),
        CONF_VARIABLES: _normalize_variables(payload.get(CONF_VARIABLES, [])),
        CONF_DEVICE_PROFILES: ensure_profile_ids(payload.get(CONF_DEVICE_PROFILES, [])),
        CONF_ENABLE_ROUTE: bool(payload.get(CONF_ENABLE_ROUTE, False)),
        CONF_ROUTE_NAME: str(payload.get(CONF_ROUTE_NAME, "")),
        CONF_ROUTE_USERNAME: str(payload.get(CONF_ROUTE_USERNAME, "")),
        CONF_ROUTE_PASSWORD: route_password,
        CONF_SENDER_AMS: str(payload.get(CONF_SENDER_AMS, "")),
    }


def _build_entry_options_from_payload(
    payload: dict[str, Any],
    route_password: str,
) -> dict[str, Any]:
    """Rakenna config entryn options importoitavasta payloadista."""
    options = {
        CONF_VARIABLES: _normalize_variables(payload.get(CONF_VARIABLES, [])),
        CONF_DEVICE_PROFILES: ensure_profile_ids(payload.get(CONF_DEVICE_PROFILES, [])),
        CONF_ENABLE_ROUTE: bool(payload.get(CONF_ENABLE_ROUTE, False)),
        CONF_ROUTE_NAME: str(payload.get(CONF_ROUTE_NAME, "")),
        CONF_ROUTE_USERNAME: str(payload.get(CONF_ROUTE_USERNAME, "")),
        CONF_ROUTE_PASSWORD: route_password,
        CONF_SENDER_AMS: str(payload.get(CONF_SENDER_AMS, "")),
    }
    secret_key = payload.get(SETTINGS_YAML_PASSWORD_SECRET_KEY, "")
    if secret_key:
        options[CONF_ROUTE_PASSWORD_SECRET] = str(secret_key)
    return options


def _validate_import_payload(raw_entry: Any) -> dict[str, Any]:
    """Validoi import-rivin minimiavaimet."""
    if not isinstance(raw_entry, dict):
        raise HomeAssistantError("Import-entry pitää olla YAML map-rakenne.")

    required = [CONF_PLC_NAME, CONF_AMS_NET_ID, CONF_IP_ADDRESS]
    missing = [key for key in required if not str(raw_entry.get(key, "")).strip()]
    if missing:
        raise HomeAssistantError(
            f"Import-entryltä puuttuu pakollisia kenttiä: {', '.join(missing)}"
        )

    payload = dict(raw_entry)
    payload[CONF_PLC_NAME] = str(payload[CONF_PLC_NAME]).strip()
    payload[CONF_AMS_NET_ID] = str(payload[CONF_AMS_NET_ID]).strip()
    payload[CONF_IP_ADDRESS] = str(payload[CONF_IP_ADDRESS]).strip()
    payload[CONF_IP_PORT] = int(payload.get(CONF_IP_PORT, 851))
    payload[CONF_VARIABLES] = _normalize_variables(payload.get(CONF_VARIABLES, []))
    payload[CONF_DEVICE_PROFILES] = ensure_profile_ids(payload.get(CONF_DEVICE_PROFILES, []))
    payload[CONF_ENABLE_ROUTE] = bool(payload.get(CONF_ENABLE_ROUTE, False))
    payload[CONF_ROUTE_NAME] = str(payload.get(CONF_ROUTE_NAME, ""))
    payload[CONF_ROUTE_USERNAME] = str(payload.get(CONF_ROUTE_USERNAME, ""))
    payload[CONF_SENDER_AMS] = str(payload.get(CONF_SENDER_AMS, ""))
    if SETTINGS_YAML_PASSWORD_SECRET_KEY in payload:
        payload[SETTINGS_YAML_PASSWORD_SECRET_KEY] = str(
            payload.get(SETTINGS_YAML_PASSWORD_SECRET_KEY, "")
        ).strip()
    return payload


async def _async_export_settings_to_yaml(
    hass: HomeAssistant,
    path_override: str | None = None,
) -> Path:
    """Vie integraation asetukset YAML-tiedostoon."""
    entries = hass.config_entries.async_entries(DOMAIN)
    settings_path = _settings_file_path(hass, path_override)
    secrets_path = _secrets_file_path(hass)

    exported_entries: list[dict[str, Any]] = []
    secrets_payload = await hass.async_add_executor_job(_safe_read_yaml, secrets_path)

    for entry in entries:
        exported = _entry_to_export_payload(entry)
        route_password = str(_effective_option(entry, CONF_ROUTE_PASSWORD, "") or "")
        if route_password:
            secret_key = exported.get(SETTINGS_YAML_PASSWORD_SECRET_KEY) or _secret_key_for_ams(
                str(exported[CONF_AMS_NET_ID])
            )
            exported[SETTINGS_YAML_PASSWORD_SECRET_KEY] = secret_key
            secrets_payload[secret_key] = route_password
        exported_entries.append(exported)

    document = {
        SETTINGS_YAML_VERSION_KEY: SETTINGS_SCHEMA_VERSION,
        SETTINGS_YAML_EXPORTED_AT_KEY: datetime.now(UTC).isoformat(),
        SETTINGS_YAML_ENTRIES_KEY: exported_entries,
    }

    await hass.async_add_executor_job(_safe_write_yaml, settings_path, document)
    await hass.async_add_executor_job(_safe_write_yaml, secrets_path, secrets_payload)
    return settings_path


async def _async_import_settings_from_yaml(
    hass: HomeAssistant,
    path_override: str | None = None,
    overwrite_existing: bool = True,
) -> tuple[int, int]:
    """Palauta integraation asetukset YAML-tiedostosta."""
    settings_path = _settings_file_path(hass, path_override)
    secrets_path = _secrets_file_path(hass)

    document = await hass.async_add_executor_job(_safe_read_yaml, settings_path)
    raw_entries = document.get(SETTINGS_YAML_ENTRIES_KEY, [])
    if not isinstance(raw_entries, list):
        raise HomeAssistantError(
            f"Kentän '{SETTINGS_YAML_ENTRIES_KEY}' pitää olla lista tiedostossa '{settings_path}'."
        )

    secrets_payload = await hass.async_add_executor_job(_safe_read_yaml, secrets_path)

    existing_by_ams: dict[str, ConfigEntry] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        ams = str(entry.data.get(CONF_AMS_NET_ID, "")).strip()
        if ams:
            existing_by_ams[ams] = entry

    updated = 0
    created = 0
    state = _domain_state(hass)
    state[AUTO_EXPORT_GUARD_KEY] = True
    try:
        for raw_entry in raw_entries:
            payload = _validate_import_payload(raw_entry)
            ams = payload[CONF_AMS_NET_ID]

            existing_entry = existing_by_ams.get(ams)
            if existing_entry and not overwrite_existing:
                continue

            route_password = ""
            secret_key = payload.get(SETTINGS_YAML_PASSWORD_SECRET_KEY, "")
            if secret_key:
                route_password = str(secrets_payload.get(secret_key, "") or "")
                if not route_password:
                    _LOGGER.warning(
                        "Secret-avain '%s' puuttuu tiedostosta %s (PLC %s).",
                        secret_key,
                        secrets_path,
                        ams,
                    )
            if not route_password and existing_entry:
                route_password = str(
                    _effective_option(existing_entry, CONF_ROUTE_PASSWORD, "") or ""
                )

            if existing_entry:
                new_data = _build_entry_data_from_payload(payload, route_password)
                new_options = _build_entry_options_from_payload(payload, route_password)
                hass.config_entries.async_update_entry(
                    existing_entry,
                    title=payload[CONF_PLC_NAME],
                    data=new_data,
                    options=new_options,
                )
                await hass.config_entries.async_reload(existing_entry.entry_id)
                updated += 1
                continue

            flow_result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data={**payload, CONF_ROUTE_PASSWORD: route_password},
            )
            if flow_result.get("type") == "create_entry":
                created += 1
            elif flow_result.get("type") == "abort":
                _LOGGER.warning(
                    "Asetusten import keskeytyi PLC:lle %s (syy: %s)",
                    ams,
                    flow_result.get("reason"),
                )
            else:
                _LOGGER.warning(
                    "Asetusten import PLC:lle %s palautti odottamattoman tuloksen: %s",
                    ams,
                    flow_result.get("type"),
                )
    finally:
        state[AUTO_EXPORT_GUARD_KEY] = False

    await _async_export_settings_to_yaml(hass)
    return updated, created


async def _async_register_services(hass: HomeAssistant) -> None:
    """Rekisteröi integraation service actionit."""
    state = _domain_state(hass)
    if state.get(SERVICES_REGISTERED_KEY):
        return

    export_schema = vol.Schema(
        {
            vol.Optional(SERVICE_FIELD_FILE_PATH): str,
        }
    )
    import_schema = vol.Schema(
        {
            vol.Optional(SERVICE_FIELD_FILE_PATH): str,
            vol.Optional(SERVICE_FIELD_OVERWRITE_EXISTING, default=True): bool,
        }
    )

    async def _handle_export(call: ServiceCall) -> None:
        settings_path = await _async_export_settings_to_yaml(
            hass,
            call.data.get(SERVICE_FIELD_FILE_PATH),
        )
        _LOGGER.info("ADS Multi asetukset viety tiedostoon: %s", settings_path)

    async def _handle_import(call: ServiceCall) -> None:
        updated, created = await _async_import_settings_from_yaml(
            hass,
            call.data.get(SERVICE_FIELD_FILE_PATH),
            bool(call.data.get(SERVICE_FIELD_OVERWRITE_EXISTING, True)),
        )
        _LOGGER.info(
            "ADS Multi asetukset palautettu YAML:stä (päivitetty=%s, luotu=%s)",
            updated,
            created,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_SETTINGS,
        _handle_export,
        schema=export_schema,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_SETTINGS,
        _handle_import,
        schema=import_schema,
    )
    state[SERVICES_REGISTERED_KEY] = True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Alusta integraation domain-tason palvelut."""
    _domain_state(hass)
    await _async_register_services(hass)
    return True


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


def _normalize_variables(variables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalisoi muuttujalista ja lisää puuttuvat oletusarvot."""
    normalized: list[dict[str, Any]] = []
    for variable in variables:
        item = dict(variable)
        item[CONF_ASYNC_READ] = bool(item.get(CONF_ASYNC_READ, False))
        normalized.append(item)
    return normalized


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Alusta yksi PLC-yhteys config entrystä."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_services(hass)

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
    variables = _normalize_variables(
        entry.options.get(CONF_VARIABLES) or entry.data.get(CONF_VARIABLES, [])
    )
    device_profiles = ensure_profile_ids(
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

    try:
        # Ota ADS notificationit käyttöön async-luentaan valituille muuttujille
        await coordinator.async_setup_notifications()
        # Ensimmäinen päivitys – heittää ConfigEntryNotReady jos epäonnistuu
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
        "pyads_version": _get_pyads_version(),
    }

    state = _domain_state(hass)
    if not state.get(AUTO_EXPORT_GUARD_KEY, False):
        try:
            await _async_export_settings_to_yaml(hass)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.warning("Asetusten automaattinen YAML-vienti epäonnistui: %s", err)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Lataa entry uudelleen kun asetukset muuttuvat."""
    state = _domain_state(hass)
    if not state.get(AUTO_EXPORT_GUARD_KEY, False):
        try:
            await _async_export_settings_to_yaml(hass)
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
        self.poll_read_points, self._configured_async_read_points = self._split_read_points(
            self.read_points
        )
        self._read_point_type_by_name = {
            str(point["name"]): str(point["type"]).upper() for point in self.read_points
        }
        self._poll_point_keys = {
            (point["name"], point["type"]) for point in self.poll_read_points
        }
        self._notification_handles: dict[str, tuple[int, int]] = {}
        self._notification_callbacks: dict[str, Any] = {}
        self._failed_async_subscriptions: set[str] = set()
        self._async_values: dict[str, Any] = {}

    @property
    def configured_async_variable_count(self) -> int:
        """Palauta kuinka monta muuttujaa on asetettu async-luentaan."""
        return len(self._configured_async_read_points)

    @property
    def active_async_subscription_count(self) -> int:
        """Palauta aktiivisten ADS-notification tilausten määrä."""
        return len(self._notification_handles)

    @property
    def failed_async_subscription_count(self) -> int:
        """Palauta epäonnistuneiden async-tilausten määrä."""
        return len(self._failed_async_subscriptions)

    async def async_setup_notifications(self) -> None:
        """Luo ADS notification -tilaukset async-merkityille muuttujille."""
        if not self._configured_async_read_points:
            return
        await self.hass.async_add_executor_job(self._setup_notifications)

    async def async_release_notifications(self) -> None:
        """Vapauta ADS notification -tilaukset."""
        if not self._notification_handles:
            return
        await self.hass.async_add_executor_job(self._release_notifications)

    def _setup_notifications(self) -> None:
        """Luo ADS notificationit synkronisesti executor-säikeessä."""
        if not hasattr(self.plc, "add_device_notification") or not hasattr(
            self.plc, "del_device_notification"
        ):
            _LOGGER.warning(
                "PLC '%s': pyads notification API puuttuu, käytetään polling-luentaa.",
                self.plc_name,
            )
            for point in self._configured_async_read_points:
                self._register_poll_fallback(point)
                self._failed_async_subscriptions.add(point["name"])
            return

        if not hasattr(pyads, "NotificationAttrib") or not hasattr(self.plc, "notification"):
            _LOGGER.warning(
                "PLC '%s': pyads NotificationAttrib/decorator puuttuu, käytetään polling-luentaa.",
                self.plc_name,
            )
            for point in self._configured_async_read_points:
                self._register_poll_fallback(point)
                self._failed_async_subscriptions.add(point["name"])
            return

        for point in self._configured_async_read_points:
            var_name = point["name"]
            var_type = point["type"]
            plc_type = self._ads_type(var_type)
            try:
                attr = pyads.NotificationAttrib(self._notification_size(plc_type))
                decorator = self.plc.notification(plc_type)

                @decorator
                def _notification_callback(
                    handle,
                    name,
                    timestamp,
                    value,
                    _var_name: str = var_name,
                ):
                    self._schedule_notification_update(_var_name, value)

                handles = self.plc.add_device_notification(var_name, attr, _notification_callback)
                if isinstance(handles, tuple):
                    notification_handle = int(handles[0])
                    user_handle = int(handles[1]) if len(handles) > 1 else 0
                else:
                    notification_handle, user_handle = int(handles), 0

                self._notification_handles[var_name] = (notification_handle, user_handle)
                self._notification_callbacks[var_name] = _notification_callback

                # Alusta arvo kerran, jotta entiteetin tila on heti käyttökelpoinen
                try:
                    self._async_values[var_name] = self.plc.read_by_name(var_name, plc_type)
                except Exception as read_err:
                    _LOGGER.debug(
                        "PLC '%s': async-muuttujan '%s' alkuarvon luku epäonnistui: %s",
                        self.plc_name,
                        var_name,
                        read_err,
                    )
                _LOGGER.debug(
                    "PLC '%s': async-tilaus luotu muuttujalle '%s' (type=%s).",
                    self.plc_name,
                    var_name,
                    var_type,
                )
            except Exception as err:
                self._failed_async_subscriptions.add(var_name)
                self._register_poll_fallback(point)
                _LOGGER.warning(
                    "PLC '%s': async-tilauksen luonti epäonnistui muuttujalle '%s': %s. "
                    "Käytetään pollingia tälle muuttujalle.",
                    self.plc_name,
                    var_name,
                    err,
                )

    def _release_notifications(self) -> None:
        """Poista ADS notificationit synkronisesti executor-säikeessä."""
        for var_name, handles in list(self._notification_handles.items()):
            try:
                self.plc.del_device_notification(*handles)
            except Exception as err:
                _LOGGER.warning(
                    "PLC '%s': async-tilauksen vapautus epäonnistui muuttujalle '%s': %s",
                    self.plc_name,
                    var_name,
                    err,
                )
            finally:
                self._notification_handles.pop(var_name, None)
                self._notification_callbacks.pop(var_name, None)

    def _register_poll_fallback(self, point: dict[str, str]) -> None:
        """Lisää muuttuja pollattavaksi, jos async-tilaus epäonnistui."""
        key = (point["name"], point["type"])
        if key in self._poll_point_keys:
            return
        self._poll_point_keys.add(key)
        self.poll_read_points.append({"name": point["name"], "type": point["type"]})

    @staticmethod
    def _notification_size(plc_type: Any) -> int:
        """Laske ilmoituksen tavumäärä PLC-tyypille."""
        try:
            return ctypes.sizeof(plc_type)
        except Exception:
            return 4

    def _schedule_notification_update(self, var_name: str, value: Any) -> None:
        """Aja notification-päivitys säikeiturvallisesti HA event loopissa."""
        self.hass.loop.call_soon_threadsafe(
            self._handle_notification_update,
            var_name,
            value,
        )

    def _handle_notification_update(self, var_name: str, value: Any) -> None:
        """Päivitä koordinaattorin data notificationin saapuessa."""
        if var_name not in self._notification_handles:
            return
        normalized_value = self._normalize_notification_value(var_name, value)
        self._async_values[var_name] = normalized_value
        updated_data = dict(self.data or {})
        updated_data[var_name] = normalized_value
        self.async_set_updated_data(updated_data)

    def _normalize_notification_value(self, var_name: str, value: Any) -> Any:
        """Normalisoi notification-arvo määritellyn ADS-tyypin mukaan."""
        var_type = self._read_point_type_by_name.get(var_name, "")
        if var_type == "BOOL":
            return self._normalize_bool_value(value)

        if hasattr(value, "value"):
            return value.value
        return value

    @staticmethod
    def _normalize_bool_value(value: Any) -> bool | None:
        """Normalisoi BOOL-arvo yleisimmistä pyads callback -muodoista."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (bytes, bytearray)):
            if not value:
                return False
            return bool(value[0])
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "on"}:
                return True
            if normalized in {"false", "0", "off", ""}:
                return False
        if hasattr(value, "value"):
            return AdsPlcCoordinator._normalize_bool_value(value.value)
        return bool(value)

    async def _async_update_data(self) -> dict[str, Any]:
        """Päivitä data: synkroniset muuttujat pollingilla, asyncit notificationeilla."""
        try:
            return await self.hass.async_add_executor_job(self._read_polled_variables)
        except pyads.ADSError as err:
            raise UpdateFailed(
                f"PLC '{self.plc_name}' lukuvirhe: {err}"
            ) from err

    def _read_polled_variables(self) -> dict[str, Any]:
        """Synkroninen polling-luku (ajetaan executor-säikeessä)."""
        data: dict[str, Any] = dict(self.data or {})
        for point in self.poll_read_points:
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

        # Async-tilauksilta tulleet viimeisimmät arvot pidetään mukana
        data.update(self._async_values)
        return data

    @staticmethod
    def _build_read_points(
        variables: list[dict[str, Any]],
        device_profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Yhdistä luettavat pointit ja merkkaa luentatapa (poll/async)."""
        points_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        light_profile_point_keys = {
            (point["name"], point["type"])
            for point in collect_profile_points(
                get_profiles_by_type(device_profiles, PROFILE_TYPE_LIGHT)
            )
        }

        for var in variables:
            name = str(var.get("name", ""))
            var_type = str(var.get("type", "")).upper()
            if not name or not var_type:
                continue
            key = (name, var_type)
            use_async = bool(var.get(CONF_ASYNC_READ, False))
            if key in points_by_key:
                # Jos sama pointti näkyy useasti, async voittaa
                points_by_key[key][CONF_ASYNC_READ] = (
                    points_by_key[key][CONF_ASYNC_READ] or use_async
                )
            else:
                points_by_key[key] = {
                    "name": name,
                    "type": var_type,
                    CONF_ASYNC_READ: use_async,
                }

        for point in collect_profile_points(device_profiles):
            key = (point["name"], point["type"])
            profile_async = key in light_profile_point_keys
            if key in points_by_key:
                # Light-profiilien tilat luetaan aina asyncina, jotta UI päivittyy heti
                points_by_key[key][CONF_ASYNC_READ] = (
                    points_by_key[key][CONF_ASYNC_READ] or profile_async
                )
            else:
                points_by_key[key] = {
                    "name": point["name"],
                    "type": point["type"],
                    CONF_ASYNC_READ: profile_async,
                }

        return list(points_by_key.values())

    @staticmethod
    def _split_read_points(
        points: list[dict[str, Any]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Jaa luettavat pointit pollattaviin ja async-tilattaviin."""
        poll_points: list[dict[str, str]] = []
        async_points: list[dict[str, str]] = []
        for point in points:
            normalized = {"name": str(point["name"]), "type": str(point["type"]).upper()}
            if bool(point.get(CONF_ASYNC_READ, False)):
                async_points.append(normalized)
            else:
                poll_points.append(normalized)
        return poll_points, async_points

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
