"""Binary sensor -platform: BOOL-muuttujat vain luku -tilassa."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_AMS_NET_ID, ATTR_PLC_NAME, ATTR_VAR_NAME, ATTR_VAR_TYPE, DOMAIN
from . import AdsPlcCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Luo binary sensor -entiteetit BOOL-muuttujille (ei-kirjoitettavat)."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AdsPlcCoordinator = data["coordinator"]
    variables: list[dict[str, Any]] = data["variables"]

    entities = [
        AdsPlcBinarySensor(coordinator, var, entry)
        for var in variables
        if var["type"].upper() == "BOOL" and not var.get("writable", False)
    ]
    async_add_entities(entities)


class AdsPlcBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """BOOL-muuttuja binary sensor -entiteettinä (read-only)."""

    _attr_has_entity_name = False

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

        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{var_name}_bs"
        self._attr_name = friendly

    @property
    def is_on(self) -> bool | None:
        """Palauta tila."""
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self._variable["name"])
        return bool(val) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Lisäattribuutit."""
        return {
            ATTR_PLC_NAME: self.coordinator.plc_name,
            ATTR_AMS_NET_ID: self.coordinator.ams_net_id,
            ATTR_VAR_NAME: self._variable["name"],
            ATTR_VAR_TYPE: "BOOL",
        }
