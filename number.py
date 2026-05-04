"""Number-platform: kirjoitettavat numeeriset muuttujat liukusäätimiksi."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_AMS_NET_ID, ATTR_PLC_NAME, ATTR_VAR_NAME, ATTR_VAR_TYPE, DOMAIN
from .coordinator import AdsPlcCoordinator

NUMERIC_TYPES = {"BYTE", "WORD", "DWORD", "INT", "DINT", "REAL", "LREAL"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Luo number-entiteetit kirjoitettaville numeerisille muuttujille."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AdsPlcCoordinator = data["coordinator"]
    variables: list[dict[str, Any]] = data["variables"]

    entities = [
        AdsPlcNumber(coordinator, var, entry)
        for var in variables
        if var["type"].upper() in NUMERIC_TYPES and var.get("writable", False)
    ]
    async_add_entities(entities)


class AdsPlcNumber(CoordinatorEntity, NumberEntity):
    """Numeerinen PLC-muuttuja liukusäätimenä."""

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

        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{var_name}_num"
        self._attr_name = friendly
        self._attr_native_unit_of_measurement = variable.get("unit") or None
        self._attr_mode = NumberMode.BOX

        # Rajoitukset config-datasta tai oletusarvot tyypin mukaan
        var_type = variable["type"].upper()
        if var_type in ("REAL", "LREAL"):
            self._attr_native_min_value = variable.get("min", -1e6)
            self._attr_native_max_value = variable.get("max", 1e6)
            self._attr_native_step = variable.get("step", 0.1)
        elif var_type in ("INT", "DINT"):
            self._attr_native_min_value = variable.get("min", -32768)
            self._attr_native_max_value = variable.get("max", 32767)
            self._attr_native_step = variable.get("step", 1)
        else:  # BYTE, WORD, DWORD
            self._attr_native_min_value = variable.get("min", 0)
            self._attr_native_max_value = variable.get("max", 65535)
            self._attr_native_step = variable.get("step", 1)

    @property
    def native_value(self) -> float | None:
        """Palauta arvo."""
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self._variable["name"])
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Kirjoita uusi arvo PLC:lle."""
        var_name: str = self._variable["name"]
        var_type: str = self._variable["type"]
        coordinator: AdsPlcCoordinator = self.coordinator

        # Kokonaislukutyypit → cast int
        if var_type.upper() in ("BYTE", "WORD", "DWORD", "INT", "DINT"):
            write_value = int(value)
        else:
            write_value = value

        await self.hass.async_add_executor_job(
            coordinator.write_variable, var_name, var_type, write_value
        )
        if coordinator.data is not None:
            coordinator.data[var_name] = write_value
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Lisäattribuutit."""
        return {
            ATTR_PLC_NAME: self.coordinator.plc_name,
            ATTR_AMS_NET_ID: self.coordinator.ams_net_id,
            ATTR_VAR_NAME: self._variable["name"],
            ATTR_VAR_TYPE: self._variable["type"],
        }
