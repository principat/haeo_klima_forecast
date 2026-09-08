"""Sensor entities: forecast output for HAEO/energy optimization, plus recorder-mirror sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfIrradiance, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INDOOR_UNITS, CONF_NAME, DOMAIN, IU_CLIMATE_ENTITY, IU_NAME
from .coordinator import HaeoForecastCoordinator
from .mirror import (
    SUFFIX_ACTIVE,
    SUFFIX_CURRENT_TEMPERATURE,
    SUFFIX_HVAC_ACTION,
    SUFFIX_HVAC_MODE,
    SUFFIX_SETPOINT,
    WEATHER_SUFFIX_OUTDOOR_TEMPERATURE,
    WEATHER_SUFFIX_SHORTWAVE_RADIATION,
    WEATHER_SUFFIX_WIND_SPEED,
    climate_mirror_unique_id,
    encode_hvac_action,
    encode_hvac_mode,
    weather_mirror_unique_id,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HaeoForecastCoordinator = hass.data[DOMAIN][entry.entry_id]
    system_name = entry.data.get(CONF_NAME, entry.title)

    entities: list[SensorEntity] = [
        HaeoForecastSensor(coordinator, entry),
        HaeoWeightsSensor(coordinator, entry),
    ]

    for unit in coordinator.config.get(CONF_INDOOR_UNITS, []):
        climate_entity_id = unit.get(IU_CLIMATE_ENTITY)
        if not climate_entity_id:
            continue
        unit_name = unit.get(IU_NAME, climate_entity_id)
        entities.extend(_build_climate_mirror_sensors(hass, entry, system_name, unit_name, climate_entity_id))

    entities.extend(_build_weather_mirror_sensors(coordinator, entry, system_name))

    async_add_entities(entities)


def _build_climate_mirror_sensors(
    hass: HomeAssistant, entry: ConfigEntry, system_name: str, unit_name: str, climate_entity_id: str
) -> list[SensorEntity]:
    return [
        ClimateMirrorSensor(
            hass, entry, system_name, unit_name, climate_entity_id,
            suffix=SUFFIX_SETPOINT,
            friendly="Setpoint",
            extractor=lambda state: _to_float(state.attributes.get("temperature")),
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        ClimateMirrorSensor(
            hass, entry, system_name, unit_name, climate_entity_id,
            suffix=SUFFIX_CURRENT_TEMPERATURE,
            friendly="Current Temperature",
            extractor=lambda state: _to_float(state.attributes.get("current_temperature")),
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        ClimateMirrorSensor(
            hass, entry, system_name, unit_name, climate_entity_id,
            suffix=SUFFIX_ACTIVE,
            friendly="Active",
            extractor=lambda state: 0.0 if state.state in ("off", "unavailable", "unknown") else 1.0,
        ),
        ClimateMirrorSensor(
            hass, entry, system_name, unit_name, climate_entity_id,
            suffix=SUFFIX_HVAC_MODE,
            friendly="HVAC Mode",
            extractor=lambda state: encode_hvac_mode(state.state),
        ),
        ClimateMirrorSensor(
            hass, entry, system_name, unit_name, climate_entity_id,
            suffix=SUFFIX_HVAC_ACTION,
            friendly="HVAC Action",
            extractor=lambda state: encode_hvac_action(state.attributes.get("hvac_action")),
        ),
    ]


def _build_weather_mirror_sensors(
    coordinator: HaeoForecastCoordinator, entry: ConfigEntry, system_name: str
) -> list[SensorEntity]:
    return [
        WeatherMirrorSensor(
            coordinator, entry, system_name,
            suffix=WEATHER_SUFFIX_OUTDOOR_TEMPERATURE,
            friendly="Outdoor Temperature",
            attribute="temperature_c",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        ),
        WeatherMirrorSensor(
            coordinator, entry, system_name,
            suffix=WEATHER_SUFFIX_SHORTWAVE_RADIATION,
            friendly="Shortwave Radiation",
            attribute="shortwave_radiation",
            device_class=SensorDeviceClass.IRRADIANCE,
            native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        ),
        WeatherMirrorSensor(
            coordinator, entry, system_name,
            suffix=WEATHER_SUFFIX_WIND_SPEED,
            friendly="Wind Speed",
            attribute="wind_speed_ms",
            device_class=SensorDeviceClass.WIND_SPEED,
            native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        ),
    ]


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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


class _MirrorEntity(SensorEntity):
    """Common bits for recorder-mirror sensors, see mirror.py.

    Hidden by default and marked diagnostic: these exist so the recorder
    builds long-term statistics for them, not for people to look at directly.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, system_name: str) -> None:
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=system_name,
            manufacturer="HAEO Klima Forecast",
        )


class ClimateMirrorSensor(_MirrorEntity):
    """Republishes one numeric value derived from a climate entity as a `sensor`.

    `climate` entities never get long-term statistics (their state is a HVAC
    mode string, not a measurement), so their history is only available for
    `recorder.purge_keep_days`. This sensor mirrors one derived value with
    `state_class: measurement`, so HA statistics keep it indefinitely.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        system_name: str,
        unit_name: str,
        climate_entity_id: str,
        *,
        suffix: str,
        friendly: str,
        extractor,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
    ) -> None:
        super().__init__(entry, system_name)
        self.hass = hass
        self._climate_entity_id = climate_entity_id
        self._extractor = extractor
        self._attr_unique_id = climate_mirror_unique_id(climate_entity_id, suffix)
        self._attr_name = f"{system_name} {unit_name} {friendly} (recorded)"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_native_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._climate_entity_id], self._handle_source_event
            )
        )
        state = self.hass.states.get(self._climate_entity_id)
        if state is not None:
            self._apply(state)

    @callback
    def _handle_source_event(self, event: Event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._apply(new_state)
        self.async_write_ha_state()

    def _apply(self, state) -> None:
        self._attr_native_value = self._extractor(state)


class WeatherMirrorSensor(CoordinatorEntity[HaeoForecastCoordinator], _MirrorEntity):
    """Republishes the most recently fetched ('now') weather point as a `sensor`.

    Fed by the coordinator on every update cycle (see coordinator.py), this
    builds HA's own long-term-statistics archive for the outdoor conditions
    going forward, so `weighting.py` does not have to rely solely on the
    weather provider's historical archive API for training data.
    """

    def __init__(
        self,
        coordinator: HaeoForecastCoordinator,
        entry: ConfigEntry,
        system_name: str,
        *,
        suffix: str,
        friendly: str,
        attribute: str,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        _MirrorEntity.__init__(self, entry, system_name)
        self._attribute = attribute
        self._attr_unique_id = weather_mirror_unique_id(entry.entry_id, suffix)
        self._attr_name = f"{system_name} {friendly} (recorded)"
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement

    @property
    def native_value(self) -> float | None:
        point = self.coordinator.latest_weather_point
        if point is None:
            return None
        return getattr(point, self._attribute, None)
