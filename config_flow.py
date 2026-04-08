"""Config flow – lisää uusi PLC Home Assistantin käyttöliittymästä."""
from __future__ import annotations

import logging
import re
import socket
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
    CONF_ENABLE_ROUTE,
    CONF_SENDER_AMS,
    CONF_ROUTE_USERNAME,
    CONF_ROUTE_PASSWORD,
    CONF_ROUTE_NAME,
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


def _resolve_sender_ams(target_ip: str) -> str:
    """Muodosta lähettäjän AMS Net ID paikallisen lähde-IP:n perusteella."""
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
    return f"{local_ip}.1.1"


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
            # Validointi: salasana ilman käyttäjänimeä
            password = user_input.get(CONF_ROUTE_PASSWORD, "").strip()
            username = user_input.get(CONF_ROUTE_USERNAME, "").strip()
            if password and not username:
                errors[CONF_ROUTE_USERNAME] = "password_without_username"
            # Validoi AMS Net ID -formaatti
            elif not AMS_NET_ID_PATTERN.match(user_input[CONF_AMS_NET_ID]):
                errors[CONF_AMS_NET_ID] = "invalid_ams_net_id"
            else:
                sender_ams = _resolve_sender_ams(user_input[CONF_IP_ADDRESS])
                # Testaa yhteys (route-parametrit välitetään testille)
                can_connect = await self.hass.async_add_executor_job(
                    _test_connection,
                    user_input[CONF_AMS_NET_ID],
                    user_input[CONF_IP_ADDRESS],
                    user_input.get(CONF_IP_PORT, DEFAULT_PORT),
                    {
                        "enable_route": user_input.get(CONF_ENABLE_ROUTE, False),
                        "sender_ams": sender_ams,
                        "route_name": user_input.get(CONF_ROUTE_NAME, ""),
                        "username": username,
                        "password": password,
                    }
                )
                if not can_connect:
                    errors["base"] = "cannot_connect"
                else:
                    # Tarkista ettei samaa PLC:tä ole jo lisätty
                    await self.async_set_unique_id(user_input[CONF_AMS_NET_ID])
                    self._abort_if_unique_id_configured()

                    # Tallenna kaikki parametrit (mukaan lukien route)
                    self._plc_data = user_input
                    self._plc_data[CONF_SENDER_AMS] = sender_ams
                    # Siivoa route-parametrit
                    self._plc_data[CONF_ROUTE_USERNAME] = username
                    self._plc_data[CONF_ROUTE_PASSWORD] = password
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
            if action == "manage_route":
                return await self.async_step_manage_route()
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
                            "manage_route": "Muokkaa route-konfiguraatiota",
                            "finish": "Tallenna ja sulje",
                        }
                    )
                }
            ),
            description_placeholders={"variables": names},
        )

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
                    or _resolve_sender_ams(self.config_entry.data[CONF_IP_ADDRESS])
                )
                # Tallenna route-konfiguraatio
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_VARIABLES: self._variables,
                        CONF_ENABLE_ROUTE: user_input.get(CONF_ENABLE_ROUTE, False),
                        CONF_ROUTE_NAME: user_input.get(CONF_ROUTE_NAME, ""),
                        CONF_ROUTE_USERNAME: username,
                        CONF_ROUTE_PASSWORD: password,
                        CONF_SENDER_AMS: sender_ams,
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
        return self.async_create_entry(
            title="",
            data={
                CONF_VARIABLES: self._variables,
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
                    or _resolve_sender_ams(self.config_entry.data[CONF_IP_ADDRESS]),
                ),
            },
        )


def _test_connection(
    ams_net_id: str,
    ip_address: str,
    ip_port: int,
    route_config: dict[str, Any] | None = None,
) -> bool:
    """Testaa ADS-yhteys (synkroninen) ja lisää reitin tarvittaessa."""
    _LOGGER.debug("_test_connection aloitettu: ams_net_id=%s, ip=%s:%s", ams_net_id, ip_address, ip_port)
    _LOGGER.debug("route_config: %s", route_config)

    # Lisää reitti ensin jos konfiguroitu
    if route_config and route_config.get("enable_route"):
        _LOGGER.info("Route-lisääminen aktivoitu testissa")
        try:
            sender_ams = route_config["sender_ams"]
            route_name = route_config.get("route_name") or f"HA-{ams_net_id}"
            username = route_config.get("username") or ""
            password = route_config.get("password") or ""

            _LOGGER.debug("Route parametrit: sender=%s, target=%s, ip=%s, route_name=%s",
                         sender_ams, ams_net_id, ip_address, route_name)

            _LOGGER.debug("Avataan pyads portti...")
            pyads.open_port()
            _LOGGER.debug("Portti avattu")

            try:
                _LOGGER.debug("Asetetaan local address: %s", sender_ams)
                pyads.set_local_address(sender_ams)
                _LOGGER.debug("Local address asetettu")

                _LOGGER.info("Lisätään route: sender=%s, target=%s, ip=%s", sender_ams, ams_net_id, ip_address)
                pyads.add_route_to_plc(
                    sender_ams,
                    ip_address,
                    ip_address,
                    username,
                    password,
                    route_name=route_name,
                )
                _LOGGER.info("Reitti '%s' lisätty onnistuneesti: %s -> %s", route_name, sender_ams, ams_net_id)
            finally:
                _LOGGER.debug("Suljetaan pyads portti...")
                pyads.close_port()
                _LOGGER.debug("Portti suljettu")
        except Exception as err:
            _LOGGER.error("Route-lisääminen epäonnistui testissa: %s", err, exc_info=True)
            return False

    # Testaa yhteys
    try:
        _LOGGER.debug("Avataan yhteys testille: %s", ams_net_id)
        plc = pyads.Connection(ams_net_id, ip_port, ip_address)
        plc.open()
        _LOGGER.debug("Yhteys avattu, luetaan tilaa...")
        plc.read_state()
        plc.close()
        _LOGGER.info("Yhteyden testaus onnistui: %s", ams_net_id)
        return True
    except Exception as err:
        _LOGGER.exception("ADS-yhteystesti epäonnistui (%s / %s): %s", ams_net_id, ip_address, err)
        return False
