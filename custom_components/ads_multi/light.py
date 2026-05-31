"""Light-platform: profiilipohjaiset valot (on/off, himmennys, valolämpötila)."""
from __future__ import annotations

from typing import Any

from homeassistant.components import light as light_comp
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_AMS_NET_ID,
    ATTR_PLC_NAME,
    DOMAIN,
    CONF_PROFILE_TYPE,
    LIGHT_KEY_BRIGHTNESS,
    LIGHT_KEY_COLOR_TEMP,
    LIGHT_KEY_ON_OFF,
    PROFILE_KEY_ID,
    PROFILE_KEY_MAX,
    PROFILE_KEY_MIN,
    PROFILE_KEY_NAME,
    PROFILE_KEY_TYPE,
    PROFILE_TYPE_LIGHT,
)
from .coordinator import AdsPlcCoordinator
from .entity_profiles import get_profiles_by_type, slugify_profile_id

INTEGER_TYPES = {"BYTE", "WORD", "DWORD", "INT", "DINT"}
ATTR_BRIGHTNESS = light_comp.ATTR_BRIGHTNESS
ATTR_COLOR_TEMP = getattr(light_comp, "ATTR_COLOR_TEMP", "color_temp")
ATTR_COLOR_TEMP_KELVIN = getattr(light_comp, "ATTR_COLOR_TEMP_KELVIN", "color_temp_kelvin")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Luo light-entiteetit profiileista."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: AdsPlcCoordinator = data["coordinator"]
    device_profiles: list[dict[str, Any]] = data.get("device_profiles", [])
    light_profiles = get_profiles_by_type(device_profiles, PROFILE_TYPE_LIGHT)

    entities = [
        AdsPlcLight(coordinator, profile, entry, index)
        for index, profile in enumerate(light_profiles)
    ]
    async_add_entities(entities)


class AdsPlcLight(CoordinatorEntity, LightEntity):
    """Yksi PLC-valo, joka voi sisältää useita ADS-pointteja."""

    def __init__(
        self,
        coordinator: AdsPlcCoordinator,
        profile: dict[str, Any],
        entry: ConfigEntry,
        index: int,
    ) -> None:
        """Alusta."""
        super().__init__(coordinator)
        self._profile = profile
        self._on_off = profile[LIGHT_KEY_ON_OFF]
        self._brightness = profile.get(LIGHT_KEY_BRIGHTNESS)
        self._color_temp = profile.get(LIGHT_KEY_COLOR_TEMP)

        profile_name: str = profile.get(PROFILE_KEY_NAME, f"Light {index + 1}")
        profile_id = str(profile.get(PROFILE_KEY_ID, "")).strip() or str(profile_name)
        unique_slug = slugify_profile_id(profile_id)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{unique_slug}_light"
        self._attr_name = profile_name

        if self._color_temp:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif self._brightness:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def is_on(self) -> bool | None:
        """Palauta valon on/off-tila."""
        value = self._read_value(self._on_off)
        return bool(value) if value is not None else None

    @property
    def brightness(self) -> int | None:
        """Palauta kirkkaus Home Assistantin 0..255-asteikolla."""
        if not self._brightness:
            return None
        value = self._read_value(self._brightness)
        if value is None:
            return None
        return self._scale_to_ha(
            float(value),
            float(self._brightness[PROFILE_KEY_MIN]),
            float(self._brightness[PROFILE_KEY_MAX]),
        )

    @property
    def color_temp_kelvin(self) -> int | None:
        """Palauta valolämpötila kelvineinä."""
        if not self._color_temp:
            return None
        value = self._read_value(self._color_temp)
        if value is None:
            return None
        return int(round(float(value)))

    @property
    def min_color_temp_kelvin(self) -> int | None:
        """Palauta alin sallittu valolämpötila kelvineinä."""
        if not self._color_temp:
            return None
        return int(round(float(self._color_temp[PROFILE_KEY_MIN])))

    @property
    def max_color_temp_kelvin(self) -> int | None:
        """Palauta ylin sallittu valolämpötila kelvineinä."""
        if not self._color_temp:
            return None
        return int(round(float(self._color_temp[PROFILE_KEY_MAX])))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Kytke valo päälle ja aseta mahdolliset parametrit."""
        if self._brightness and ATTR_BRIGHTNESS in kwargs:
            brightness = int(kwargs[ATTR_BRIGHTNESS])
            target = self._scale_from_ha(
                brightness,
                float(self._brightness[PROFILE_KEY_MIN]),
                float(self._brightness[PROFILE_KEY_MAX]),
            )
            await self._write_value(self._brightness, target)

        if self._color_temp:
            kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
            if kelvin is None and ATTR_COLOR_TEMP in kwargs:
                mired = int(kwargs[ATTR_COLOR_TEMP])
                if mired > 0:
                    kelvin = int(round(1_000_000 / mired))
            if kelvin is not None:
                kelvin_value = self._clamp(
                    float(kelvin),
                    float(self._color_temp[PROFILE_KEY_MIN]),
                    float(self._color_temp[PROFILE_KEY_MAX]),
                )
                await self._write_value(self._color_temp, kelvin_value)

        await self._write_value(self._on_off, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Kytke valo pois päältä."""
        await self._write_value(self._on_off, False)
        self.async_write_ha_state()

    def _read_value(self, point: dict[str, Any]) -> Any:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(point[PROFILE_KEY_NAME])

    async def _write_value(self, point: dict[str, Any], value: Any) -> None:
        var_name = str(point[PROFILE_KEY_NAME])
        var_type = str(point[PROFILE_KEY_TYPE]).upper()
        cast_value = self._cast_for_type(value, var_type)
        await self.hass.async_add_executor_job(
            self.coordinator.write_variable,
            var_name,
            var_type,
            cast_value,
        )
        if self.coordinator.data is not None:
            self.coordinator.data[var_name] = cast_value

    @staticmethod
    def _cast_for_type(value: Any, var_type: str) -> Any:
        if var_type in INTEGER_TYPES:
            return int(round(float(value)))
        if var_type == "BOOL":
            return bool(value)
        return float(value)

    @staticmethod
    def _scale_to_ha(value: float, min_value: float, max_value: float) -> int:
        if max_value <= min_value:
            return 0
        normalized = (value - min_value) / (max_value - min_value)
        return int(round(AdsPlcLight._clamp(normalized, 0.0, 1.0) * 255))

    @staticmethod
    def _scale_from_ha(value: int, min_value: float, max_value: float) -> float:
        normalized = AdsPlcLight._clamp(float(value) / 255.0, 0.0, 1.0)
        return min_value + normalized * (max_value - min_value)

    @staticmethod
    def _clamp(value: float, min_value: float, max_value: float) -> float:
        return max(min_value, min(max_value, value))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Lisäattribuutit."""
        attrs: dict[str, Any] = {
            ATTR_PLC_NAME: self.coordinator.plc_name,
            ATTR_AMS_NET_ID: self.coordinator.ams_net_id,
            CONF_PROFILE_TYPE: self._profile.get(CONF_PROFILE_TYPE),
            "on_off_symbol": self._on_off.get(PROFILE_KEY_NAME),
        }
        if self._brightness:
            attrs["brightness_symbol"] = self._brightness.get(PROFILE_KEY_NAME)
            attrs["brightness_min"] = self._brightness.get(PROFILE_KEY_MIN)
            attrs["brightness_max"] = self._brightness.get(PROFILE_KEY_MAX)
        if self._color_temp:
            attrs["color_temp_symbol"] = self._color_temp.get(PROFILE_KEY_NAME)
            attrs["color_temp_min"] = self._color_temp.get(PROFILE_KEY_MIN)
            attrs["color_temp_max"] = self._color_temp.get(PROFILE_KEY_MAX)
        return attrs
