"""DataUpdateCoordinator yhdelle Beckhoff PLC -yhteydelle."""

from __future__ import annotations

import ctypes
import logging
from threading import RLock
from typing import TYPE_CHECKING, Any

import pyads
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ads_connection import ads_type
from .const import CONF_ASYNC_READ, PROFILE_TYPE_LIGHT
from .entity_profiles import collect_profile_points, get_profiles_by_type

if TYPE_CHECKING:
    from datetime import timedelta

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class AdsPlcCoordinator(DataUpdateCoordinator):
    """Koordinaattori yhdelle Beckhoff PLC:lle."""

    # Explicit PLC connection and read configuration are kept together.
    def __init__(  # noqa: PLR0913
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
        # All synchronous connection operations run in executor threads.
        # Notification callbacks only schedule updates and must not take this lock.
        self._plc_lock = RLock()
        self.plc_name = plc_name
        self.ams_net_id = ams_net_id
        self.variables = variables
        self.device_profiles = device_profiles
        self.read_points = self._build_read_points(variables, device_profiles)
        self.poll_read_points, self._configured_async_read_points = (
            self._split_read_points(self.read_points)
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
        with self._plc_lock:
            if not hasattr(self.plc, "add_device_notification") or not hasattr(
                self.plc, "del_device_notification"
            ):
                _LOGGER.warning(
                    "PLC '%s': pyads notification API puuttuu, "
                    "käytetään polling-luentaa.",
                    self.plc_name,
                )
                for point in self._configured_async_read_points:
                    self._register_poll_fallback(point)
                    self._failed_async_subscriptions.add(point["name"])
                return

            if not hasattr(pyads, "NotificationAttrib") or not hasattr(
                self.plc, "notification"
            ):
                _LOGGER.warning(
                    "PLC '%s': pyads NotificationAttrib/decorator puuttuu, "
                    "käytetään polling-luentaa.",
                    self.plc_name,
                )
                for point in self._configured_async_read_points:
                    self._register_poll_fallback(point)
                    self._failed_async_subscriptions.add(point["name"])
                return

            for point in self._configured_async_read_points:
                var_name = point["name"]
                var_type = point["type"]
                plc_type = ads_type(var_type)
                try:
                    attr = pyads.NotificationAttrib(self._notification_size(plc_type))
                    decorator = self.plc.notification(plc_type)

                    @decorator
                    def _notification_callback(
                        _handle: int,
                        _name: str,
                        _timestamp: Any,
                        value: Any,
                        _var_name: str = var_name,
                    ) -> None:
                        self._schedule_notification_update(_var_name, value)

                    handles = self.plc.add_device_notification(
                        var_name, attr, _notification_callback
                    )
                    if isinstance(handles, tuple):
                        notification_handle = int(handles[0])
                        user_handle = int(handles[1]) if len(handles) > 1 else 0
                    else:
                        notification_handle, user_handle = int(handles), 0

                    self._notification_handles[var_name] = (
                        notification_handle,
                        user_handle,
                    )
                    self._notification_callbacks[var_name] = _notification_callback

                    try:
                        self._async_values[var_name] = self.plc.read_by_name(
                            var_name, plc_type
                        )
                    except Exception as read_err:  # noqa: BLE001 - Notifications may still deliver values.
                        _LOGGER.debug(
                            "PLC '%s': async-muuttujan '%s' "
                            "alkuarvon luku epäonnistui: %s",
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
                except Exception as err:  # noqa: BLE001 - Isolate subscription failures per variable.
                    self._failed_async_subscriptions.add(var_name)
                    self._register_poll_fallback(point)
                    _LOGGER.warning(
                        "PLC '%s': async-tilauksen luonti epäonnistui "
                        "muuttujalle '%s': %s. "
                        "Käytetään pollingia tälle muuttujalle.",
                        self.plc_name,
                        var_name,
                        err,
                    )

    def _release_notifications(self) -> None:
        """Poista ADS notificationit synkronisesti executor-säikeessä."""
        with self._plc_lock:
            for var_name, handles in list(self._notification_handles.items()):
                try:
                    self.plc.del_device_notification(*handles)
                except Exception as err:  # noqa: BLE001 - Isolate subscription failures per variable.
                    _LOGGER.warning(
                        "PLC '%s': async-tilauksen vapautus epäonnistui "
                        "muuttujalle '%s': %s",
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
        except Exception:  # noqa: BLE001 - Preserve fallback for unsupported ctypes types.
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
            return bool(value and value[0])
        if isinstance(value, str):
            normalized = value.strip().lower()
            known_values = {
                "true": True,
                "1": True,
                "on": True,
                "false": False,
                "0": False,
                "off": False,
                "": False,
            }
            if normalized in known_values:
                return known_values[normalized]
        if hasattr(value, "value"):
            return AdsPlcCoordinator._normalize_bool_value(value.value)
        return bool(value)

    async def _async_update_data(self) -> dict[str, Any]:
        """Päivitä data pollingilla ja ADS-notificationeilla."""
        try:
            return await self.hass.async_add_executor_job(self._read_polled_variables)
        except pyads.ADSError as err:
            msg = f"PLC '{self.plc_name}' lukuvirhe: {err}"
            raise UpdateFailed(msg) from err

    def _read_polled_variables(self) -> dict[str, Any]:
        """Synkroninen polling-luku executor-säikeessä."""
        with self._plc_lock:
            data: dict[str, Any] = dict(self.data or {})
            for point in self.poll_read_points:
                var_name: str = point["name"]
                var_type: str = point["type"]
                try:
                    value = self.plc.read_by_name(var_name, ads_type(var_type))
                    data[var_name] = value
                except pyads.ADSError as err:
                    _LOGGER.warning(
                        "PLC '%s': muuttujaa '%s' ei voitu lukea: %s",
                        self.plc_name,
                        var_name,
                        err,
                    )
                    if self.data and var_name in self.data:
                        data[var_name] = self.data[var_name]
                    else:
                        data[var_name] = None

            data.update(self._async_values)
            return data

    @staticmethod
    def _build_read_points(
        variables: list[dict[str, Any]],
        device_profiles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Yhdistä luettavat pointit ja merkkaa luentatapa."""
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
            normalized = {
                "name": str(point["name"]),
                "type": str(point["type"]).upper(),
            }
            if bool(point.get(CONF_ASYNC_READ, False)):
                async_points.append(normalized)
            else:
                poll_points.append(normalized)
        return poll_points, async_points

    def write_variable(self, var_name: str, var_type: str, value: Any) -> None:
        """Kirjoita arvo PLC:lle executor-säikeessä."""
        with self._plc_lock:
            self.plc.write_by_name(var_name, value, ads_type(var_type))

    def close_connection(self) -> None:
        """Release subscriptions and close after pending ADS work finishes."""
        with self._plc_lock:
            self._release_notifications()
            self.plc.close()
