"""Config flow – lisää uusi PLC Home Assistantin käyttöliittymästä."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .ads_connection import test_connection
from .const import (
    CONF_AMS_NET_ID,
    CONF_ASYNC_READ,
    CONF_DEVICE_PROFILES,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_PROFILE_TYPE,
    CONF_VARIABLES,
    CONF_ENABLE_ROUTE,
    CONF_SENDER_AMS,
    CONF_ROUTE_USERNAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_NAME,
    CONF_ROUTE_PASSWORD_SECRET,
    LIGHT_KEY_BRIGHTNESS,
    LIGHT_KEY_COLOR_TEMP,
    LIGHT_KEY_ON_OFF,
    DEFAULT_SETTINGS_FILE,
    DEFAULT_PORT,
    DOMAIN,
    PROFILE_KEY_MAX,
    PROFILE_KEY_ID,
    PROFILE_KEY_MIN,
    PROFILE_KEY_NAME,
    PROFILE_KEY_TYPE,
    PROFILE_TYPE_LIGHT,
    SERVICE_EXPORT_SETTINGS,
    SERVICE_IMPORT_SETTINGS,
    SERVICE_FIELD_FILE_PATH,
    SERVICE_FIELD_OVERWRITE_EXISTING,
)
from .entity_profiles import ensure_profile_ids, slugify_profile_id
from .helpers import normalize_variables, resolve_sender_ams

# AMS Net ID -formaatti: x.x.x.x.x.x (kuusi numeroa pisteiden välissä)
AMS_NET_ID_PATTERN = re.compile(
    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
)

PLC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLC_NAME): str,
        vol.Required(CONF_AMS_NET_ID): str,
        vol.Required(CONF_IP_ADDRESS): str,
        vol.Optional(CONF_IP_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(CONF_ENABLE_ROUTE, default=False): bool,
        vol.Optional(CONF_ROUTE_USERNAME, default=""): str,
        vol.Optional(CONF_ROUTE_PASSWORD, default=""): str,
        vol.Optional(CONF_ROUTE_NAME, default=""): str,
    }
)

# Yhden muuttujan lisäyskaavio
VARIABLE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): str,
        vol.Required("type"): vol.In(
            ["BOOL", "BYTE", "WORD", "DWORD", "INT", "DINT", "REAL", "LREAL", "STRING"]
        ),
        vol.Optional("friendly_name", default=""): str,
        vol.Optional("unit", default=""): str,
        vol.Optional("device_class", default=""): str,
        vol.Optional("writable", default=False): bool,
        vol.Optional(CONF_ASYNC_READ, default=False): bool,
    }
)

LIGHT_NUMERIC_TYPES = ["BYTE", "WORD", "DWORD", "INT", "DINT", "REAL", "LREAL"]

LIGHT_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("profile_name"): str,
        vol.Required("on_off_symbol"): str,
        vol.Optional("on_off_type", default="BOOL"): vol.In(["BOOL"]),
        vol.Optional("brightness_symbol", default=""): str,
        vol.Optional("brightness_type", default="INT"): vol.In(LIGHT_NUMERIC_TYPES),
        vol.Optional("brightness_min", default=0): vol.Coerce(float),
        vol.Optional("brightness_max", default=100): vol.Coerce(float),
        vol.Optional("color_temp_symbol", default=""): str,
        vol.Optional("color_temp_type", default="INT"): vol.In(LIGHT_NUMERIC_TYPES),
        vol.Optional("color_temp_min", default=2700): vol.Coerce(float),
        vol.Optional("color_temp_max", default=6500): vol.Coerce(float),
    }
)

# Route-konfiguraation kaavio
ROUTE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ENABLE_ROUTE, default=False): bool,
        vol.Optional(CONF_ROUTE_NAME, default=""): str,
        vol.Optional(CONF_ROUTE_USERNAME, default=""): str,
        vol.Optional(CONF_ROUTE_PASSWORD, default=""): str,
    }
)


class AdsMultiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-vaiheinen config flow PLC:n lisäykselle."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Vaihe 1: PLC:n yhteystiedot."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validointi: salasana ilman käyttäjänimeä
            password = user_input.get(CONF_ROUTE_PASSWORD, "").strip()
            username = user_input.get(CONF_ROUTE_USERNAME, "").strip()
            if password and not username:
                errors[CONF_ROUTE_USERNAME] = "password_without_username"
            # Validoi AMS Net ID -formaatti
            elif not AMS_NET_ID_PATTERN.match(user_input[CONF_AMS_NET_ID]):
                errors[CONF_AMS_NET_ID] = "invalid_ams_net_id"
            else:
                sender_ams = resolve_sender_ams(user_input[CONF_IP_ADDRESS])
                # Testaa yhteys (route-parametrit välitetään testille)
                can_connect = await self.hass.async_add_executor_job(
                    test_connection,
                    user_input[CONF_AMS_NET_ID],
                    user_input[CONF_IP_ADDRESS],
                    user_input.get(CONF_IP_PORT, DEFAULT_PORT),
                    {
                        CONF_ENABLE_ROUTE: user_input.get(CONF_ENABLE_ROUTE, False),
                        CONF_SENDER_AMS: sender_ams,
                        CONF_ROUTE_NAME: user_input.get(CONF_ROUTE_NAME, ""),
                        CONF_ROUTE_USERNAME: username,
                        CONF_ROUTE_PASSWORD: password,
                    }
                )
                if not can_connect:
                    errors["base"] = "cannot_connect"
                else:
                    # Tarkista ettei samaa PLC:tä ole jo lisätty
                    await self.async_set_unique_id(user_input[CONF_AMS_NET_ID])
                    self._abort_if_unique_id_configured()

                    # Luo entry heti ilman pakollista muuttujan lisäystä.
                    plc_data = dict(user_input)
                    plc_data[CONF_SENDER_AMS] = sender_ams
                    plc_data[CONF_ROUTE_USERNAME] = username
                    plc_data[CONF_ROUTE_PASSWORD] = password
                    return self.async_create_entry(
                        title=plc_data[CONF_PLC_NAME],
                        data={
                            **plc_data,
                            CONF_VARIABLES: [],
                            CONF_DEVICE_PROFILES: [],
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=PLC_SCHEMA,
            errors=errors,
            description_placeholders={
                "example_ams": "192.168.1.100.1.1",
                "example_ip": "192.168.1.100",
            },
        )

    async def async_step_import(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Luo config entry YAML-importista."""
        if user_input is None:
            return self.async_abort(reason="invalid_import")

        ams_net_id = str(user_input.get(CONF_AMS_NET_ID, "")).strip()
        plc_name = str(user_input.get(CONF_PLC_NAME, "")).strip()
        ip_address = str(user_input.get(CONF_IP_ADDRESS, "")).strip()
        if not ams_net_id or not plc_name or not ip_address:
            return self.async_abort(reason="invalid_import")

        await self.async_set_unique_id(ams_net_id)
        self._abort_if_unique_id_configured()

        data = {
            CONF_PLC_NAME: plc_name,
            CONF_AMS_NET_ID: ams_net_id,
            CONF_IP_ADDRESS: ip_address,
            CONF_IP_PORT: int(user_input.get(CONF_IP_PORT, DEFAULT_PORT)),
            CONF_VARIABLES: normalize_variables(user_input.get(CONF_VARIABLES, [])),
            CONF_DEVICE_PROFILES: ensure_profile_ids(user_input.get(CONF_DEVICE_PROFILES, [])),
            CONF_ENABLE_ROUTE: bool(user_input.get(CONF_ENABLE_ROUTE, False)),
            CONF_ROUTE_NAME: str(user_input.get(CONF_ROUTE_NAME, "")),
            CONF_ROUTE_USERNAME: str(user_input.get(CONF_ROUTE_USERNAME, "")).strip(),
            CONF_ROUTE_PASSWORD: str(user_input.get(CONF_ROUTE_PASSWORD, "")).strip(),
            CONF_SENDER_AMS: str(user_input.get(CONF_SENDER_AMS) or resolve_sender_ams(ip_address)),
        }
        secret_key = str(user_input.get(CONF_ROUTE_PASSWORD_SECRET, "")).strip()
        if secret_key:
            data[CONF_ROUTE_PASSWORD_SECRET] = secret_key

        return self.async_create_entry(
            title=plc_name,
            data=data,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Palauta options flow muuttujien hallintaan."""
        return AdsMultiOptionsFlow()


class AdsMultiOptionsFlow(config_entries.OptionsFlow):
    """Options flow: hallinnoi muuttujia jälkikäteen."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Päävalikko: valitse toiminto."""
        # Alustetaan tässä koska __init__ ei ole käytössä uusissa HA-versioissa
        if not hasattr(self, "_variables"):
            self._variables = normalize_variables(
                self.config_entry.options.get(CONF_VARIABLES)
                or self.config_entry.data.get(CONF_VARIABLES, [])
            )
        if not hasattr(self, "_device_profiles"):
            self._device_profiles = ensure_profile_ids(
                self.config_entry.options.get(CONF_DEVICE_PROFILES)
                or self.config_entry.data.get(CONF_DEVICE_PROFILES, [])
            )

        names = ", ".join(v["name"] for v in self._variables) or "–"
        lights = ", ".join(
            p.get(PROFILE_KEY_NAME, "")
            for p in self._device_profiles
            if p.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT
        ) or "–"
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "add_variable": "Lisää muuttuja",
                "remove_variable": "Poista muuttuja",
                "add_light": "Lisää valo",
                "edit_light_select": "Muokkaa valoa",
                "remove_light": "Poista valo",
                "manage_route": "Muokkaa route-konfiguraatiota",
                "export_settings": "Vie asetukset YAML-tiedostoon",
                "import_settings": "Palauta asetukset YAML-tiedostosta",
                "finish": "Tallenna ja sulje",
            },
            description_placeholders={"variables": names, "lights": lights},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Tallenna asetukset ja sulje options flow."""
        return await self._save_and_finish()

    async def async_step_manage_route(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Hallinnoi route-konfiguraatiota."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validointi: salasana ilman käyttäjänimeä
            password = user_input.get(CONF_ROUTE_PASSWORD, "").strip()
            username = user_input.get(CONF_ROUTE_USERNAME, "").strip()
            if password and not username:
                errors[CONF_ROUTE_USERNAME] = "password_without_username"
            else:
                sender_ams = (
                    self.config_entry.options.get(CONF_SENDER_AMS)
                    or self.config_entry.data.get(CONF_SENDER_AMS)
                    or resolve_sender_ams(self.config_entry.data[CONF_IP_ADDRESS])
                )
                # Tallenna route-konfiguraatio
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_VARIABLES: self._variables,
                        CONF_DEVICE_PROFILES: self._device_profiles,
                        CONF_ENABLE_ROUTE: user_input.get(CONF_ENABLE_ROUTE, False),
                        CONF_ROUTE_NAME: user_input.get(CONF_ROUTE_NAME, ""),
                        CONF_ROUTE_USERNAME: username,
                        CONF_ROUTE_PASSWORD: password,
                        CONF_SENDER_AMS: sender_ams,
                        CONF_ROUTE_PASSWORD_SECRET: self.config_entry.options.get(
                            CONF_ROUTE_PASSWORD_SECRET,
                            self.config_entry.data.get(CONF_ROUTE_PASSWORD_SECRET, ""),
                        ),
                    },
                )

        # Hae nykyiset route-asetukset config_entrystä
        current_route_config = {
            CONF_ENABLE_ROUTE: self.config_entry.options.get(CONF_ENABLE_ROUTE)
            or self.config_entry.data.get(CONF_ENABLE_ROUTE, False),
            CONF_ROUTE_NAME: self.config_entry.options.get(CONF_ROUTE_NAME)
            or self.config_entry.data.get(CONF_ROUTE_NAME, ""),
            CONF_ROUTE_USERNAME: self.config_entry.options.get(CONF_ROUTE_USERNAME)
            or self.config_entry.data.get(CONF_ROUTE_USERNAME, ""),
            CONF_ROUTE_PASSWORD: self.config_entry.options.get(CONF_ROUTE_PASSWORD)
            or self.config_entry.data.get(CONF_ROUTE_PASSWORD, ""),
        }

        return self.async_show_form(
            step_id="manage_route",
            data_schema=ROUTE_CONFIG_SCHEMA.extend(
                {vol.Optional(CONF_ENABLE_ROUTE, default=current_route_config[CONF_ENABLE_ROUTE]): bool}
            ),
            errors=errors,
            description_placeholders={
                "current_settings": str(current_route_config),
            },
        )

    async def async_step_export_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Vie integraation asetukset YAML-tiedostoon."""
        errors: dict[str, str] = {}
        if user_input is not None:
            file_path = str(user_input.get(SERVICE_FIELD_FILE_PATH, "")).strip()
            service_data: dict[str, Any] = {}
            if file_path:
                service_data[SERVICE_FIELD_FILE_PATH] = file_path
            try:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_EXPORT_SETTINGS,
                    service_data,
                    blocking=True,
                )
                return await self.async_step_init()
            except HomeAssistantError:
                errors["base"] = "export_failed"

        return self.async_show_form(
            step_id="export_settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(SERVICE_FIELD_FILE_PATH, default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={"default_path": f"/config/{DEFAULT_SETTINGS_FILE}"},
        )

    async def async_step_import_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Palauta integraation asetukset YAML-tiedostosta."""
        errors: dict[str, str] = {}
        if user_input is not None:
            file_path = str(user_input.get(SERVICE_FIELD_FILE_PATH, "")).strip()
            overwrite_existing = bool(user_input.get(SERVICE_FIELD_OVERWRITE_EXISTING, True))
            service_data: dict[str, Any] = {
                SERVICE_FIELD_OVERWRITE_EXISTING: overwrite_existing,
            }
            if file_path:
                service_data[SERVICE_FIELD_FILE_PATH] = file_path
            try:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_IMPORT_SETTINGS,
                    service_data,
                    blocking=True,
                )
                return await self.async_step_init()
            except HomeAssistantError:
                errors["base"] = "import_failed"

        return self.async_show_form(
            step_id="import_settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(SERVICE_FIELD_FILE_PATH, default=""): str,
                    vol.Optional(SERVICE_FIELD_OVERWRITE_EXISTING, default=True): bool,
                }
            ),
            errors=errors,
            description_placeholders={"default_path": f"/config/{DEFAULT_SETTINGS_FILE}"},
        )

    async def async_step_add_variable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Lisää uusi muuttuja."""
        errors: dict[str, str] = {}

        if user_input is not None:
            existing_names = {v["name"] for v in self._variables}
            if user_input["name"] in existing_names:
                errors["name"] = "variable_exists"
            else:
                self._variables.append(user_input)
                return await self._save_and_finish()

        return self.async_show_form(
            step_id="add_variable",
            data_schema=VARIABLE_SCHEMA,
            errors=errors,
        )

    async def async_step_remove_variable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Poista muuttuja."""
        if user_input is not None:
            name_to_remove = user_input.get("variable_name")
            self._variables = [v for v in self._variables if v["name"] != name_to_remove]
            return await self._save_and_finish()

        names = [v["name"] for v in self._variables]
        if not names:
            return await self._save_and_finish()

        return self.async_show_form(
            step_id="remove_variable",
            data_schema=vol.Schema(
                {vol.Required("variable_name"): vol.In(names)}
            ),
        )

    async def async_step_add_light(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Lisää uusi light-profiili."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, profile = self._validate_and_build_light_profile(user_input)
            if not errors and profile is not None:
                self._device_profiles.append(profile)
                return await self._save_and_finish()

        return self.async_show_form(
            step_id="add_light",
            data_schema=LIGHT_PROFILE_SCHEMA,
            errors=errors,
        )

    async def async_step_edit_light_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Valitse muokattava light-profiili."""
        light_names = [
            str(p.get(PROFILE_KEY_NAME))
            for p in self._device_profiles
            if p.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT and p.get(PROFILE_KEY_NAME)
        ]
        if not light_names:
            return await self.async_step_init()

        if user_input is not None:
            self._edit_light_original_name = user_input.get("light_name")
            return await self.async_step_edit_light()

        return self.async_show_form(
            step_id="edit_light_select",
            data_schema=vol.Schema({vol.Required("light_name"): vol.In(light_names)}),
        )

    async def async_step_edit_light(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Muokkaa olemassa olevaa light-profiilia."""
        original_name = getattr(self, "_edit_light_original_name", None)
        if not original_name:
            return await self.async_step_init()

        profile = self._find_light_profile(original_name)
        if profile is None:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        if user_input is not None:
            errors, updated_profile = self._validate_and_build_light_profile(
                user_input,
                current_name=original_name,
            )
            if not errors and updated_profile is not None:
                for idx, current in enumerate(self._device_profiles):
                    if (
                        current.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT
                        and current.get(PROFILE_KEY_NAME) == original_name
                    ):
                        self._device_profiles[idx] = updated_profile
                        break
                return await self._save_and_finish()

        defaults = self._light_form_defaults(profile)
        return self.async_show_form(
            step_id="edit_light",
            data_schema=vol.Schema(
                {
                    vol.Required("profile_name", default=defaults["profile_name"]): str,
                    vol.Required("on_off_symbol", default=defaults["on_off_symbol"]): str,
                    vol.Optional("on_off_type", default=defaults["on_off_type"]): vol.In(["BOOL"]),
                    vol.Optional("brightness_symbol", default=defaults["brightness_symbol"]): str,
                    vol.Optional("brightness_type", default=defaults["brightness_type"]): vol.In(LIGHT_NUMERIC_TYPES),
                    vol.Optional("brightness_min", default=defaults["brightness_min"]): vol.Coerce(float),
                    vol.Optional("brightness_max", default=defaults["brightness_max"]): vol.Coerce(float),
                    vol.Optional("color_temp_symbol", default=defaults["color_temp_symbol"]): str,
                    vol.Optional("color_temp_type", default=defaults["color_temp_type"]): vol.In(LIGHT_NUMERIC_TYPES),
                    vol.Optional("color_temp_min", default=defaults["color_temp_min"]): vol.Coerce(float),
                    vol.Optional("color_temp_max", default=defaults["color_temp_max"]): vol.Coerce(float),
                }
            ),
            errors=errors,
        )

    async def async_step_remove_light(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Poista light-profiili."""
        light_names = [
            str(p.get(PROFILE_KEY_NAME))
            for p in self._device_profiles
            if p.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT and p.get(PROFILE_KEY_NAME)
        ]
        if not light_names:
            return await self._save_and_finish()

        if user_input is not None:
            profile_name = user_input.get("light_name")
            self._device_profiles = [
                p
                for p in self._device_profiles
                if not (
                    p.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT
                    and p.get(PROFILE_KEY_NAME) == profile_name
                )
            ]
            return await self._save_and_finish()

        return self.async_show_form(
            step_id="remove_light",
            data_schema=vol.Schema({vol.Required("light_name"): vol.In(light_names)}),
        )

    async def _save_and_finish(self) -> FlowResult:
        """Tallenna päivitetyt muuttujat options-kenttään."""
        return self.async_create_entry(
            title="",
            data={
                CONF_VARIABLES: self._variables,
                CONF_DEVICE_PROFILES: self._device_profiles,
                CONF_ENABLE_ROUTE: self.config_entry.options.get(
                    CONF_ENABLE_ROUTE,
                    self.config_entry.data.get(CONF_ENABLE_ROUTE, False),
                ),
                CONF_ROUTE_NAME: self.config_entry.options.get(
                    CONF_ROUTE_NAME,
                    self.config_entry.data.get(CONF_ROUTE_NAME, ""),
                ),
                CONF_ROUTE_USERNAME: self.config_entry.options.get(
                    CONF_ROUTE_USERNAME,
                    self.config_entry.data.get(CONF_ROUTE_USERNAME, ""),
                ),
                CONF_ROUTE_PASSWORD: self.config_entry.options.get(
                    CONF_ROUTE_PASSWORD,
                    self.config_entry.data.get(CONF_ROUTE_PASSWORD, ""),
                ),
                CONF_SENDER_AMS: self.config_entry.options.get(
                    CONF_SENDER_AMS,
                    self.config_entry.data.get(CONF_SENDER_AMS)
                    or resolve_sender_ams(self.config_entry.data[CONF_IP_ADDRESS]),
                ),
                CONF_ROUTE_PASSWORD_SECRET: self.config_entry.options.get(
                    CONF_ROUTE_PASSWORD_SECRET,
                    self.config_entry.data.get(CONF_ROUTE_PASSWORD_SECRET, ""),
                ),
            },
        )

    def _find_light_profile(self, name: str) -> dict[str, Any] | None:
        """Hae light-profiili nimen perusteella."""
        for profile in self._device_profiles:
            if (
                profile.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT
                and profile.get(PROFILE_KEY_NAME) == name
            ):
                return profile
        return None

    @staticmethod
    def _light_form_defaults(profile: dict[str, Any]) -> dict[str, Any]:
        """Muodosta oletusarvot light-lomakkeelle profiilista."""
        on_off = profile.get(LIGHT_KEY_ON_OFF, {})
        brightness = profile.get(LIGHT_KEY_BRIGHTNESS, {})
        color_temp = profile.get(LIGHT_KEY_COLOR_TEMP, {})
        return {
            "profile_name": profile.get(PROFILE_KEY_NAME, ""),
            "on_off_symbol": on_off.get(PROFILE_KEY_NAME, ""),
            "on_off_type": on_off.get(PROFILE_KEY_TYPE, "BOOL"),
            "brightness_symbol": brightness.get(PROFILE_KEY_NAME, ""),
            "brightness_type": brightness.get(PROFILE_KEY_TYPE, "INT"),
            "brightness_min": brightness.get(PROFILE_KEY_MIN, 0),
            "brightness_max": brightness.get(PROFILE_KEY_MAX, 100),
            "color_temp_symbol": color_temp.get(PROFILE_KEY_NAME, ""),
            "color_temp_type": color_temp.get(PROFILE_KEY_TYPE, "INT"),
            "color_temp_min": color_temp.get(PROFILE_KEY_MIN, 2700),
            "color_temp_max": color_temp.get(PROFILE_KEY_MAX, 6500),
        }

    def _validate_and_build_light_profile(
        self,
        user_input: dict[str, Any],
        current_name: str | None = None,
    ) -> tuple[dict[str, str], dict[str, Any] | None]:
        """Validoi light-lomake ja rakenna profiili."""
        errors: dict[str, str] = {}

        profile_name = user_input["profile_name"].strip()
        on_off_symbol = user_input["on_off_symbol"].strip()
        if not profile_name:
            errors["profile_name"] = "light_name_required"
            return errors, None
        if not on_off_symbol:
            errors["on_off_symbol"] = "light_on_off_required"
            return errors, None

        existing = {
            p.get(PROFILE_KEY_NAME, "").strip().lower()
            for p in self._device_profiles
            if p.get(CONF_PROFILE_TYPE) == PROFILE_TYPE_LIGHT
        }
        if current_name:
            existing.discard(current_name.strip().lower())

        if profile_name.lower() in existing:
            errors["profile_name"] = "light_exists"
            return errors, None

        brightness_symbol = user_input.get("brightness_symbol", "").strip()
        color_temp_symbol = user_input.get("color_temp_symbol", "").strip()
        brightness_min = float(user_input.get("brightness_min", 0))
        brightness_max = float(user_input.get("brightness_max", 100))
        color_temp_min = float(user_input.get("color_temp_min", 2700))
        color_temp_max = float(user_input.get("color_temp_max", 6500))

        if brightness_symbol and brightness_max <= brightness_min:
            errors["brightness_max"] = "invalid_range"
            return errors, None
        if color_temp_symbol and color_temp_max <= color_temp_min:
            errors["color_temp_max"] = "invalid_range"
            return errors, None

        profile_id = ""
        if current_name:
            current_profile = self._find_light_profile(current_name)
            if current_profile is not None:
                profile_id = str(current_profile.get(PROFILE_KEY_ID, "")).strip()
        if not profile_id:
            profile_id = slugify_profile_id(profile_name)

        profile = {
            CONF_PROFILE_TYPE: PROFILE_TYPE_LIGHT,
            PROFILE_KEY_ID: profile_id,
            PROFILE_KEY_NAME: profile_name,
            LIGHT_KEY_ON_OFF: {
                PROFILE_KEY_NAME: on_off_symbol,
                PROFILE_KEY_TYPE: user_input.get("on_off_type", "BOOL"),
            },
        }
        if brightness_symbol:
            profile[LIGHT_KEY_BRIGHTNESS] = {
                PROFILE_KEY_NAME: brightness_symbol,
                PROFILE_KEY_TYPE: user_input.get("brightness_type", "INT"),
                PROFILE_KEY_MIN: brightness_min,
                PROFILE_KEY_MAX: brightness_max,
            }
        if color_temp_symbol:
            profile[LIGHT_KEY_COLOR_TEMP] = {
                PROFILE_KEY_NAME: color_temp_symbol,
                PROFILE_KEY_TYPE: user_input.get("color_temp_type", "INT"),
                PROFILE_KEY_MIN: color_temp_min,
                PROFILE_KEY_MAX: color_temp_max,
            }
        return errors, profile
