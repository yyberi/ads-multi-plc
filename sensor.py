"""Sensor-platform: lukee numeeriset ja teksti-muuttujat PLC:ltä."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_AMS_NET_ID, ATTR_PLC_NAME, ATTR_VAR_NAME, ATTR_VAR_TYPE, DOMAIN
from . import AdsPlcCoordinator

# Muuttujatyypit jotka kuuluvat sensor-platformille (ei BOOL → binary_sensor)
SENSOR_TYPES = {"BYTE", "WORD", "DWORD", "INT", "DINT", "REAL", "LREAL", "STRING", "TIME"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Luo sensor-entiteetit tämän PLC:n muuttujille."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AdsPlcCoordinator = data["coordinator"]
    variables: list[dict[str, Any]] = data["variables"]

    entities = [
        AdsPlcSensor(coordinator, var, entry)
        for var in variables
        if var["type"].upper() in SENSOR_TYPES
    ]
    async_add_entities(entities)


class AdsPlcSensor(CoordinatorEntity, SensorEntity):
    """Yksi PLC-muuttuja sensori-entiteettinä."""

    def __init__(
        self,
        coordinator: AdsPlcCoordinator,
        variable: dict[str, Any],
        entry: ConfigEntry,
    ) -> None:
        """Alusta."""
        super().__init__(coordinator)
        self._variable = variable
        self._entry = entry

        var_name: str = variable["name"]
        friendly: str = variable.get("friendly_name") or var_name
        plc_name: str = coordinator.plc_name

        # Unique ID: domain + entry_id + muuttujan nimi (uniikki per PLC)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{var_name}"
        self._attr_name = f"{plc_name} {friendly}"
        self._attr_native_unit_of_measurement = variable.get("unit") or None

        # Aseta device_class jos annettu
        dc = variable.get("device_class", "")
        if dc:
            try:
                self._attr_device_class = SensorDeviceClass(dc)
            except ValueError:
                pass

        # Ryhmitä kaikki saman PLC:n entiteetit samaan laitteeseen
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=plc_name,
            manufacturer="Beckhoff",
            model="TwinCAT PLC",
        )

    @property
    def native_value(self):
        """Palauta muuttujan nykyinen arvo koordinaattorin datasta."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._variable["name"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Lisäattribuutit: PLC-tiedot ja muuttujatyyppi."""
        return {
            ATTR_PLC_NAME: self.coordinator.plc_name,
            ATTR_AMS_NET_ID: self.coordinator.ams_net_id,
            ATTR_VAR_NAME: self._variable["name"],
            ATTR_VAR_TYPE: self._variable["type"],
        }
