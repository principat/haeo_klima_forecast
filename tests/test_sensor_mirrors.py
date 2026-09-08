"""Integration tests: the recorder-mirror sensors are created and stay in sync.

These exercise the real HA config-entry setup (via pytest-homeassistant-custom-component's
`hass` fixture) rather than mocking the integration's internals, so they would catch e.g. a
broken entity registration or a mirror sensor that never updates.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haeo_klima_forecast.const import (
    CONF_INDOOR_UNITS,
    CONF_NAME,
    CONF_POWER_SENSOR,
    DOMAIN,
    IU_CLIMATE_ENTITY,
    IU_NAME,
)
from custom_components.haeo_klima_forecast.mirror import (
    SUFFIX_ACTIVE,
    SUFFIX_CURRENT_TEMPERATURE,
    SUFFIX_HVAC_ACTION,
    SUFFIX_HVAC_MODE,
    SUFFIX_SETPOINT,
    WEATHER_SUFFIX_OUTDOOR_TEMPERATURE,
    WEATHER_SUFFIX_SHORTWAVE_RADIATION,
    WEATHER_SUFFIX_WIND_SPEED,
    climate_mirror_unique_id,
    resolve_entity_id,
    weather_mirror_unique_id,
)
from custom_components.haeo_klima_forecast.weather.base import WeatherPoint

CLIMATE_ENTITY_ID = "climate.living_room"


class _FakeProvider:
    """Stand-in weather provider: no network calls, fixed forecast."""

    async def async_get_forecast(self, hours):
        return [
            WeatherPoint(
                timestamp=datetime.now(timezone.utc),
                temperature_c=4.5,
                shortwave_radiation=123.0,
                wind_speed_ms=3.2,
            )
        ]


@pytest.fixture
async def config_entry(hass):
    hass.states.async_set(
        "sensor.house_power",
        "1.5",
        {"device_class": "power", "unit_of_measurement": "kW"},
    )
    hass.states.async_set(
        CLIMATE_ENTITY_ID,
        "heat",
        {"temperature": 21.0, "current_temperature": 19.5, "hvac_action": "heating"},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: "Test System",
            CONF_POWER_SENSOR: "sensor.house_power",
            CONF_INDOOR_UNITS: [
                {IU_NAME: "Living Room", IU_CLIMATE_ENTITY: CLIMATE_ENTITY_ID},
            ],
        },
    )
    entry.add_to_hass(hass)
    yield entry


async def test_climate_mirror_sensors_are_created_and_track_the_source(hass, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    setpoint_entity_id = resolve_entity_id(
        hass, climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_SETPOINT)
    )
    current_temp_entity_id = resolve_entity_id(
        hass, climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_CURRENT_TEMPERATURE)
    )
    active_entity_id = resolve_entity_id(
        hass, climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_ACTIVE)
    )
    mode_entity_id = resolve_entity_id(
        hass, climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_HVAC_MODE)
    )
    action_entity_id = resolve_entity_id(
        hass, climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_HVAC_ACTION)
    )
    assert None not in (
        setpoint_entity_id, current_temp_entity_id, active_entity_id, mode_entity_id, action_entity_id
    )

    # Initial state was picked up on entity add.
    assert hass.states.get(setpoint_entity_id).state == "21.0"
    assert hass.states.get(current_temp_entity_id).state == "19.5"
    assert hass.states.get(active_entity_id).state == "1.0"
    assert hass.states.get(mode_entity_id).state == "1.0"  # heat
    assert hass.states.get(action_entity_id).state == "1.0"  # heating

    # These are what let HA build long-term statistics for them (see mirror.py).
    assert hass.states.get(setpoint_entity_id).attributes["state_class"] == "measurement"

    # Changing the source climate entity propagates to the mirrors.
    hass.states.async_set(
        CLIMATE_ENTITY_ID,
        "off",
        {"temperature": 21.0, "current_temperature": 20.1},
    )
    await hass.async_block_till_done()

    assert hass.states.get(current_temp_entity_id).state == "20.1"
    assert hass.states.get(active_entity_id).state == "0.0"
    assert hass.states.get(mode_entity_id).state == "0.0"
    assert hass.states.get(action_entity_id).state == "0.0"


async def test_climate_mirror_entity_id_survives_config_entry_recreation(hass, config_entry) -> None:
    """Deleting and re-adding the integration must not orphan the recorded climate history.

    climate_mirror_unique_id() is deliberately independent of the config entry's id (see
    mirror.py), so a fresh config entry (with a fresh, random entry_id) referencing the
    same climate entity resolves to the *same* mirror entity_id - and therefore the same
    long-term-statistics rows - instead of starting a new, empty history.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    setpoint_unique_id = climate_mirror_unique_id(CLIMATE_ENTITY_ID, SUFFIX_SETPOINT)
    original_entity_id = resolve_entity_id(hass, setpoint_unique_id)
    assert original_entity_id is not None

    await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert resolve_entity_id(hass, setpoint_unique_id) is None

    new_entry = MockConfigEntry(
        domain=DOMAIN,
        data=dict(config_entry.data),
    )
    assert new_entry.entry_id != config_entry.entry_id
    new_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(new_entry.entry_id)
    await hass.async_block_till_done()

    assert resolve_entity_id(hass, setpoint_unique_id) == original_entity_id


async def test_weather_mirror_sensors_reflect_the_latest_forecast_point(
    hass, config_entry, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.coordinator.HaeoForecastCoordinator._get_provider",
        lambda self: (_FakeProvider(), _NullSession()),
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    # No trained weights yet -> refresh fails early, before the provider is even asked
    # for a forecast (see coordinator._async_update_data) - that's expected and, per
    # __init__.py, must not prevent the entry (and thus the mirror sensors) from loading.
    assert coordinator.latest_weather_point is None

    coordinator._weights_cache = {"coefficients": {}, "r2": 0.0, "n_samples": 0, "trained_at": "now"}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.latest_weather_point is not None
    assert coordinator.latest_weather_point.temperature_c == 4.5

    temp_entity_id = resolve_entity_id(
        hass, weather_mirror_unique_id(config_entry.entry_id, WEATHER_SUFFIX_OUTDOOR_TEMPERATURE)
    )
    radiation_entity_id = resolve_entity_id(
        hass, weather_mirror_unique_id(config_entry.entry_id, WEATHER_SUFFIX_SHORTWAVE_RADIATION)
    )
    wind_entity_id = resolve_entity_id(
        hass, weather_mirror_unique_id(config_entry.entry_id, WEATHER_SUFFIX_WIND_SPEED)
    )

    assert hass.states.get(temp_entity_id).state == "4.5"
    assert hass.states.get(radiation_entity_id).state == "123.0"
    # HA auto-converts sensors with device_class "wind_speed" to the configured unit
    # system's display unit (km/h here) - the recorded native value is still 3.2 m/s.
    assert hass.states.get(wind_entity_id).state == "11.52"


class _NullSession:
    async def close(self) -> None:
        return None
