"""Switch-platform: kirjoitettavat BOOL-muuttujat PLC:llä."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_AMS_NET_ID, ATTR_PLC_NAME, ATTR_VAR_NAME, ATTR_VAR_TYPE, DOMAIN
from . import AdsPlcCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Luo switch-entiteetit kirjoitettaville BOOL-muuttujille."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AdsPlcCoordinator = data["coordinator"]
    variables: list[dict[str, Any]] = data["variables"]

    entities = [
        AdsPlcSwitch(coordinator, var, entry)
        for var in variables
        if var["type"].upper() == "BOOL" and var.get("writable", False)
    ]
    async_add_entities(entities)


class AdsPlcSwitch(CoordinatorEntity, SwitchEntity):
    """BOOL-muuttuja kytkimenä – voidaan lukea ja kirjoittaa."""

    def __init__(
        self,
        coordinator: AdsPlcCoordinator,
        variable: dict[str, Any],
        entry: ConfigEntry,
    ) -> None:
        """Alusta."""
        super().__init__(coordinator)
        self._variable = variable
        var_name: str = variable["name"]
        friendly: str = variable.get("friendly_name") or var_name
        plc_name: str = coordinator.plc_name

        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{var_name}_sw"
        self._attr_name = f"{plc_name} {friendly}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=plc_name,
            manufacturer="Beckhoff",
            model="TwinCAT PLC",
        )

    @property
    def is_on(self) -> bool | None:
        """Palauta tila koordinaattorin datasta."""
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self._variable["name"])
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Kirjoita TRUE PLC:lle."""
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Kirjoita FALSE PLC:lle."""
        await self._write(False)

    async def _write(self, value: bool) -> None:
        """Kirjoita arvo ja päivitä koordinaattorin data välittömästi."""
        var_name: str = self._variable["name"]
        coordinator: AdsPlcCoordinator = self.coordinator

        await self.hass.async_add_executor_job(
            coordinator.write_variable, var_name, "BOOL", value
        )
        # Päivitä paikallinen cache välittömästi jotta UI reagoi nopeasti
        if coordinator.data is not None:
            coordinator.data[var_name] = value
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Lisäattribuutit."""
        return {
            ATTR_PLC_NAME: self.coordinator.plc_name,
            ATTR_AMS_NET_ID: self.coordinator.ams_net_id,
            ATTR_VAR_NAME: self._variable["name"],
            ATTR_VAR_TYPE: "BOOL",
        }
