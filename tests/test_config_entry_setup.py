"""End-to-end test: a fully configured entry loads all entities without crashing."""
from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
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


async def test_setup_waits_until_weather_forecast_is_available(hass, monkeypatch) -> None:
    async def _no_op_historical(self, start, end):
        return []

    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        _no_op_historical,
    )

    hass.states.async_set("sensor.house_power", "1.2", {"device_class": "power"})
    hass.states.async_set("climate.living_room", "heat", {"current_temperature": 20.5})

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

    # Weather forecast not there yet: the entry must not load, but retry later.
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get("sensor.test_system_power_forecast") is None

    hass.states.async_set(
        "sensor.weather_forecast",
        "5.0",
        {"forecast": [{"time": "2026-01-01T00:00:00+00:00", "value": 4.0}]},
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.test_system_power_forecast") is not None


async def test_weights_last_calculated_is_unknown_until_calculated(hass, monkeypatch) -> None:
    async def _no_op_historical(self, start, end):
        return []

    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        _no_op_historical,
    )
    hass.states.async_set("sensor.house_power", "1.2", {"device_class": "power"})
    hass.states.async_set("climate.living_room", "heat", {"current_temperature": 20.5})
    hass.states.async_set(
        "sensor.weather_forecast", "5.0", {"forecast": [{"time": "2026-01-01T00:00:00+00:00", "value": 4.0}]}
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

    state = hass.states.get("sensor.test_system_weights_last_calculated")
    assert state is not None
    assert state.state == "unknown"


async def test_export_history_service_and_button_write_csv(hass, hass_client, monkeypatch) -> None:
    async def _no_op_historical(self, start, end):
        return []

    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        _no_op_historical,
    )

    hass.states.async_set("sensor.house_power", "1.2", {"device_class": "power"})
    hass.states.async_set("climate.living_room", "heat", {"current_temperature": 20.5})
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

    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.history_store.async_merge(
        {
            "2026-01-01T01:00:00+00:00": {"power_kw": 2.0, "outdoor_temp": 3.5},
            "2026-01-01T00:00:00+00:00": {"power_kw": 1.0, "indoor_temp": 20.0, "outdoor_temp": 4.0},
        }
    )

    response = await hass.services.async_call(DOMAIN, "export_history", {}, blocking=True, return_response=True)

    (path,) = response["files"]
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert lines[0] == "hour_utc,power_kw,indoor_temp,outdoor_temp,humidity,wind_speed,wind_direction"
    assert lines[1] == "2026-01-01T00:00:00+00:00,1.0,20.0,4.0,,,"  # oldest first, empty cell = no value
    assert lines[2] == "2026-01-01T01:00:00+00:00,2.0,,3.5,,,"

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.test_system_export_history"}, blocking=True
    )

    client = await hass_client()
    response = await client.get(f"/api/{DOMAIN}/export/{entry.entry_id}")
    assert response.status == 200
    assert "attachment" in response.headers["Content-Disposition"]
    body = await response.text()
    assert body.splitlines()[1] == "2026-01-01T00:00:00+00:00,1.0,20.0,4.0,,,"
    assert (await client.get(f"/api/{DOMAIN}/export/unknown")).status == 404

    notification = hass.data["persistent_notification"][f"{DOMAIN}_export_{entry.entry_id}"]
    assert f"/api/{DOMAIN}/export/{entry.entry_id}?authSig=" in notification["message"]
