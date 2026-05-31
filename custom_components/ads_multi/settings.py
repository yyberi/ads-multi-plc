"""Asetusten YAML-vienti ja -palautus ADS Multi -integraatiolle."""
from __future__ import annotations

from datetime import UTC, datetime
import logging
from pathlib import Path
import re
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AMS_NET_ID,
    CONF_DEVICE_PROFILES,
    CONF_ENABLE_ROUTE,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_ROUTE_NAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_PASSWORD_SECRET,
    CONF_ROUTE_USERNAME,
    CONF_SENDER_AMS,
    CONF_VARIABLES,
    DEFAULT_SETTINGS_FILE,
    DOMAIN,
    SERVICE_EXPORT_SETTINGS,
    SERVICE_FIELD_FILE_PATH,
    SERVICE_FIELD_OVERWRITE_EXISTING,
    SERVICE_IMPORT_SETTINGS,
)
from .entity_profiles import ensure_profile_ids
from .helpers import (
    AUTO_EXPORT_GUARD_KEY,
    SERVICES_REGISTERED_KEY,
    domain_state,
    effective_option,
    normalize_variables,
)

_LOGGER = logging.getLogger(__name__)

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_YAML_ENTRIES_KEY = "entries"
SETTINGS_YAML_VERSION_KEY = "version"
SETTINGS_YAML_EXPORTED_AT_KEY = "exported_at"
SETTINGS_YAML_PASSWORD_SECRET_KEY = CONF_ROUTE_PASSWORD_SECRET


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


def _entry_to_export_payload(entry: ConfigEntry) -> dict[str, Any]:
    """Muunna config entry serialisoitavaan muotoon."""
    payload: dict[str, Any] = {
        CONF_PLC_NAME: entry.data.get(CONF_PLC_NAME, ""),
        CONF_AMS_NET_ID: entry.data.get(CONF_AMS_NET_ID, ""),
        CONF_IP_ADDRESS: entry.data.get(CONF_IP_ADDRESS, ""),
        CONF_IP_PORT: entry.data.get(CONF_IP_PORT, 851),
        CONF_VARIABLES: normalize_variables(effective_option(entry, CONF_VARIABLES, [])),
        CONF_DEVICE_PROFILES: ensure_profile_ids(
            effective_option(entry, CONF_DEVICE_PROFILES, [])
        ),
        CONF_ENABLE_ROUTE: bool(effective_option(entry, CONF_ENABLE_ROUTE, False)),
        CONF_ROUTE_NAME: effective_option(entry, CONF_ROUTE_NAME, ""),
        CONF_ROUTE_USERNAME: effective_option(entry, CONF_ROUTE_USERNAME, ""),
        CONF_SENDER_AMS: effective_option(entry, CONF_SENDER_AMS, ""),
    }
    secret_key = effective_option(entry, CONF_ROUTE_PASSWORD_SECRET, "")
    if secret_key:
        payload[SETTINGS_YAML_PASSWORD_SECRET_KEY] = str(secret_key)
    return payload


def _build_entry_data_from_payload(
    payload: dict[str, Any],
    route_password: str,
) -> dict[str, Any]:
    """Rakenna config entryn data importoitavasta payloadista."""
    return {
        CONF_PLC_NAME: payload[CONF_PLC_NAME],
        CONF_AMS_NET_ID: payload[CONF_AMS_NET_ID],
        CONF_IP_ADDRESS: payload[CONF_IP_ADDRESS],
        CONF_IP_PORT: int(payload.get(CONF_IP_PORT, 851)),
        CONF_VARIABLES: normalize_variables(payload.get(CONF_VARIABLES, [])),
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
        CONF_VARIABLES: normalize_variables(payload.get(CONF_VARIABLES, [])),
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
    payload[CONF_VARIABLES] = normalize_variables(payload.get(CONF_VARIABLES, []))
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


async def async_export_settings_to_yaml(
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
        route_password = str(effective_option(entry, CONF_ROUTE_PASSWORD, "") or "")
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


async def async_import_settings_from_yaml(
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
    state = domain_state(hass)
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
                    effective_option(existing_entry, CONF_ROUTE_PASSWORD, "") or ""
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

    await async_export_settings_to_yaml(hass)
    return updated, created


async def async_register_settings_services(hass: HomeAssistant) -> None:
    """Rekisteröi integraation asetusten service actionit."""
    state = domain_state(hass)
    if state.get(SERVICES_REGISTERED_KEY):
        return

    export_schema = vol.Schema({vol.Optional(SERVICE_FIELD_FILE_PATH): str})
    import_schema = vol.Schema(
        {
            vol.Optional(SERVICE_FIELD_FILE_PATH): str,
            vol.Optional(SERVICE_FIELD_OVERWRITE_EXISTING, default=True): bool,
        }
    )

    async def _handle_export(call: ServiceCall) -> None:
        settings_path = await async_export_settings_to_yaml(
            hass,
            call.data.get(SERVICE_FIELD_FILE_PATH),
        )
        _LOGGER.info("ADS Multi asetukset viety tiedostoon: %s", settings_path)

    async def _handle_import(call: ServiceCall) -> None:
        updated, created = await async_import_settings_from_yaml(
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
