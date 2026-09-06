"""Sensor entities: forecast output for HAEO/energy optimization."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import HaeoForecastCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HaeoForecastCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            HaeoForecastSensor(coordinator, entry),
            HaeoWeightsSensor(coordinator, entry),
        ]
    )


class _BaseEntity(CoordinatorEntity[HaeoForecastCoordinator]):
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


class HaeoForecastSensor(_BaseEntity, SensorEntity):
    """Next forecast hour as state, complete series as attribute.

    The `forecast` attribute provides the hourly prediction as a list of
    {datetime, power_kw, outdoor_temp, degree_hours, heating_degree_hours,
    cooling_degree_hours, night_setback_offset, duty_throttle_offset} - directly
    suitable to be read into HAEO (or a custom optimizer) as a power forecast.
    """

    _attr_native_unit_of_measurement = "kW"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_forecast"
        self._attr_name = f"{self._system_name} Power Forecast"

    @property
    def native_value(self):
        forecast = (self.coordinator.data or {}).get("forecast", [])
        return forecast[0]["value"] if forecast else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "forecast": data.get("forecast", []),
        }


class HaeoWeightsSensor(_BaseEntity, SensorEntity):
    """Diagnostic sensor: shows the currently active weights and training quality (R²)."""

    _attr_icon = "mdi:function-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_weights"
        self._attr_name = f"{self._system_name} Weights"

    @property
    def native_value(self):
        weights = (self.coordinator.data or {}).get("weights", {})
        r2 = weights.get("r2")
        return round(r2, 3) if r2 is not None else None

    @property
    def extra_state_attributes(self):
        weights = (self.coordinator.data or {}).get("weights", {})
        return {
            "coefficients": weights.get("coefficients", {}),
            "n_samples": weights.get("n_samples"),
            "trained_at": weights.get("trained_at"),
            "feature_names": weights.get("feature_names", []),
        }
