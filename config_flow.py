"""Config flow – lisää uusi PLC Home Assistantin käyttöliittymästä."""
from __future__ import annotations

import logging
import re
from typing import Any

import pyads
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AMS_NET_ID,
    CONF_IP_ADDRESS,
    CONF_IP_PORT,
    CONF_PLC_NAME,
    CONF_VARIABLES,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

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
    }
)


class AdsMultiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-vaiheinen config flow PLC:n lisäykselle."""

    VERSION = 1

    def __init__(self) -> None:
        """Alusta."""
        self._plc_data: dict[str, Any] = {}
        self._variables: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Vaihe 1: PLC:n yhteystiedot."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validoi AMS Net ID -formaatti
            if not AMS_NET_ID_PATTERN.match(user_input[CONF_AMS_NET_ID]):
                errors[CONF_AMS_NET_ID] = "invalid_ams_net_id"
            else:
                # Testaa yhteys
                can_connect = await self.hass.async_add_executor_job(
                    _test_connection,
                    user_input[CONF_AMS_NET_ID],
                    user_input[CONF_IP_ADDRESS],
                    user_input.get(CONF_IP_PORT, DEFAULT_PORT),
                )
                if not can_connect:
                    errors["base"] = "cannot_connect"
                else:
                    # Tarkista ettei samaa PLC:tä ole jo lisätty
                    await self.async_set_unique_id(user_input[CONF_AMS_NET_ID])
                    self._abort_if_unique_id_configured()

                    self._plc_data = user_input
                    return await self.async_step_variables()

        return self.async_show_form(
            step_id="user",
            data_schema=PLC_SCHEMA,
            errors=errors,
            description_placeholders={
                "example_ams": "192.168.1.100.1.1",
                "example_ip": "192.168.1.100",
            },
        )

    async def async_step_variables(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Vaihe 2: Lisää PLC-muuttujia. Voidaan ohittaa ja lisätä myöhemmin options flowsta."""
        errors: dict[str, str] = {}

        if user_input is not None:
            finish = user_input.get("finish", False)
            var_name = user_input.get("name", "").strip()

            # Lisää muuttuja listaan jos nimi on annettu
            if var_name:
                existing_names = {v["name"] for v in self._variables}
                if var_name in existing_names:
                    errors["name"] = "variable_exists"
                else:
                    var = {k: v for k, v in user_input.items() if k != "finish"}
                    self._variables.append(var)
                    if finish:
                        return self._create_entry()
                    return await self.async_step_variables()
            elif finish:
                # Nimi tyhjä mutta käyttäjä haluaa lopettaa – ok
                return self._create_entry()

        schema = vol.Schema(
            {
                **{k: v for k, v in VARIABLE_SCHEMA.schema.items()},
                vol.Optional("finish", default=False): bool,
            }
        )

        added = len(self._variables)
        return self.async_show_form(
            step_id="variables",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "plc_name": self._plc_data[CONF_PLC_NAME],
                "added_count": str(added),
                "added_list": ", ".join(v["name"] for v in self._variables) or "–",
            },
        )

    def _create_entry(self) -> FlowResult:
        """Luo config entry."""
        return self.async_create_entry(
            title=self._plc_data[CONF_PLC_NAME],
            data={
                **self._plc_data,
                CONF_VARIABLES: self._variables,
            },
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
            self._variables = list(
                self.config_entry.options.get(CONF_VARIABLES)
                or self.config_entry.data.get(CONF_VARIABLES, [])
            )

        if user_input is not None:
            action = user_input.get("action", "finish")
            if action == "add":
                return await self.async_step_add_variable()
            if action == "remove":
                return await self.async_step_remove_variable()
            return await self._save_and_finish()

        names = ", ".join(v["name"] for v in self._variables) or "–"
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="finish"): vol.In(
                        {
                            "add": "Lisää muuttuja",
                            "remove": "Poista muuttuja",
                            "finish": "Tallenna ja sulje",
                        }
                    )
                }
            ),
            description_placeholders={"variables": names},
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

    async def _save_and_finish(self) -> FlowResult:
        """Tallenna päivitetyt muuttujat options-kenttään."""
        return self.async_create_entry(title="", data={CONF_VARIABLES: self._variables})


def _test_connection(ams_net_id: str, ip_address: str, ip_port: int) -> bool:
    """Testaa ADS-yhteys (synkroninen)."""
    try:
        plc = pyads.Connection(ams_net_id, ip_port, ip_address)
        plc.open()
        plc.read_state()
        plc.close()
        return True
    except Exception:  # noqa: BLE001
        _LOGGER.exception("ADS-yhteystesti epäonnistui (%s / %s)", ams_net_id, ip_address)
        return False
