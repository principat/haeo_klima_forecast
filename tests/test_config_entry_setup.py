"""End-to-end test: a fully configured entry loads all entities without crashing."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.haeo_klima_forecast.const import (
    CONF_INDOOR_TEMP_SOURCE,
    CONF_NAME,
    CONF_POWER_SENSOR,
    CONF_WEATHER_FORECAST_ENTITY,
    DOMAIN,
)


async def test_full_config_entry_sets_up_forecast_and_weights_sensors(hass, monkeypatch) -> None:
    # The background HistoryStore sync would otherwise try a real network
    # call to Open-Meteo on setup - irrelevant for this test, and undesirable
    # in a sandbox without network access.
    async def _no_op_historical(self, start, end):
        return []

    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        _no_op_historical,
    )

    hass.states.async_set("sensor.house_power", "1.2", {"device_class": "power"})
    hass.states.async_set("climate.living_room", "heat", {"current_temperature": 20.5, "temperature": 21.0})
    hass.states.async_set(
        "sensor.weather_forecast",
        "5.0",
        {"forecast": [{"time": "2026-01-01T00:00:00+00:00", "value": 4.0}]},
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: "Test System",
            CONF_POWER_SENSOR: "sensor.house_power",
            CONF_WEATHER_FORECAST_ENTITY: "sensor.weather_forecast",
            CONF_INDOOR_TEMP_SOURCE: "climate.living_room",
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(f"sensor.test_system_power_forecast") is not None
    assert hass.states.get(f"sensor.test_system_weights") is not None
    assert hass.states.get(f"button.test_system_recalculate_weights") is not None
    assert hass.states.get(f"switch.test_system_weekly_weight_recalculation") is not None
