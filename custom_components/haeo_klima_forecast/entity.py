"""Shared base entity for all coordinator-backed entities of a climate system."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import HaeoForecastCoordinator


class HaeoBaseEntity(CoordinatorEntity[HaeoForecastCoordinator]):
    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._system_name = entry.data.get(CONF_NAME, entry.title)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._system_name,
            manufacturer="HAEO Klima Forecast",
        )
