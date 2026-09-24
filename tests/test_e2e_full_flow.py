"""End-to-end test of the full user journey, driven through real HA components.

Unlike the other integration tests (which use `MockConfigEntry` and stub out
individual pieces), this test exercises the actual moving parts a user would
touch:

  1. the real config-flow UI steps (`hass.config_entries.flow`), not a
     pre-built `MockConfigEntry`;
  2. a real, in-memory `recorder` component (`recorder_mock`) with imported
     long-term statistics for the power and indoor-temperature sensors -
     exactly what `history_store.py` reads via `statistics_during_period`
     in production;
  3. the real `recalculate_weights` button entity, pressed via the `button`
     domain's own service (not a direct coordinator call);
  4. the real weights/forecast sensor entities.

Only the one genuine network boundary (Open-Meteo historical weather) is
stubbed out - everything else runs as it would inside a live Home
Assistant instance, so no separate Docker/HA-container setup is needed for
this kind of end-to-end coverage.

Assertions are deliberately plausibility-based (R² in range, forecast
non-negative, colder hours forecast at least as much power as milder ones)
rather than pinned to exact regression coefficients, so the test does not
need to be rewritten every time the model itself is tuned.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from homeassistant.components.recorder.models.statistics import (
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util.unit_conversion import PowerConverter, TemperatureConverter
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.haeo_klima_forecast.const import (
    CONF_FORECAST_HOURS,
    CONF_INDOOR_TEMP_SOURCE,
    CONF_NAME,
    CONF_POWER_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_WEATHER_FORECAST_ENTITY,
    DOMAIN,
)
from custom_components.haeo_klima_forecast.weather.base import WeatherPoint

NOW = datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc)
TRAINING_DAYS = 15
BASE_LOAD_KW = 0.3
HEATING_COEFF = 0.12
INDOOR_TEMP_C = 20.5
POWER_SENSOR = "sensor.e2e_house_power"
INDOOR_SENSOR = "sensor.e2e_indoor_temp"
WEATHER_ENTITY = "sensor.e2e_weather_forecast"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Overrides the conftest-level fixture of the same name.

    Must pull in `recorder_mock` here too so its `recorder_db_url` dependency
    is resolved before the shared `hass` fixture is instantiated - the
    conftest-level autouse fixture forces `hass` first otherwise, which
    breaks `pytest_homeassistant_custom_component`'s recorder setup.
    """
    yield


def _outdoor_temp(hour: datetime) -> float:
    """Deterministic diurnal swing so the training data has real variance to fit."""
    return 5.0 + 10.0 * math.sin(2 * math.pi * hour.hour / 24)


def _hourly_marks(start: datetime, end: datetime) -> list[datetime]:
    marks = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end:
        marks.append(cur)
        cur += timedelta(hours=1)
    return marks


async def _import_hourly_statistics(
    hass, entity_id: str, name: str, unit_class: str, unit: str, values: dict[datetime, float]
) -> None:
    metadata: StatisticMetaData = {
        "source": "recorder",
        "name": name,
        "statistic_id": entity_id,
        "unit_class": unit_class,
        "unit_of_measurement": unit,
        "mean_type": StatisticMeanType.ARITHMETIC,
        "has_sum": False,
    }
    stats = [{"start": hour, "mean": value} for hour, value in values.items()]
    async_import_statistics(hass, metadata, stats)
    await async_wait_recording_done(hass)


async def test_full_user_journey_config_flow_backfill_training_and_forecast(
    hass, monkeypatch, freezer
) -> None:
    freezer.move_to(NOW)

    # ---- 1. seed real recorder statistics for the training window --------
    history_hours = _hourly_marks(NOW - timedelta(days=TRAINING_DAYS), NOW - timedelta(hours=1))
    power_values = {
        h: BASE_LOAD_KW + HEATING_COEFF * max(0.0, INDOOR_TEMP_C - _outdoor_temp(h)) for h in history_hours
    }
    indoor_values = {h: INDOOR_TEMP_C for h in history_hours}

    await _import_hourly_statistics(
        hass, POWER_SENSOR, "E2E house power", PowerConverter.UNIT_CLASS, UnitOfPower.KILO_WATT, power_values
    )
    await _import_hourly_statistics(
        hass,
        INDOOR_SENSOR,
        "E2E indoor temperature",
        TemperatureConverter.UNIT_CLASS,
        UnitOfTemperature.CELSIUS,
        indoor_values,
    )

    hass.states.async_set(POWER_SENSOR, str(power_values[history_hours[-1]]), {"device_class": "power"})
    hass.states.async_set(INDOOR_SENSOR, str(INDOOR_TEMP_C), {"device_class": "temperature"})

    # ---- 2. a forecast-template weather entity with a deliberate hot/cold
    #         contrast among the next 24 hours, to check monotonicity later
    forecast_points = []
    for i in range(1, 25):
        ts = NOW + timedelta(hours=i)
        if i == 1:
            temp = 15.0  # mild hour
        elif i == 12:
            temp = -8.0  # cold hour
        else:
            temp = _outdoor_temp(ts)
        forecast_points.append({"time": ts.isoformat(), "value": temp})
    hass.states.async_set(WEATHER_ENTITY, "5.0", {"forecast": forecast_points})

    # ---- 3. the one real external boundary: Open-Meteo historical weather
    async def _fake_historical(self, start, end):
        return [WeatherPoint(timestamp=h, temperature_c=_outdoor_temp(h)) for h in _hourly_marks(start, end)]

    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        _fake_historical,
    )

    # ---- 4. drive the actual config-flow UI steps ------------------------
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "E2E Climate System",
            CONF_POWER_SENSOR: POWER_SENSOR,
            CONF_WEATHER_FORECAST_ENTITY: WEATHER_ENTITY,
            CONF_INDOOR_TEMP_SOURCE: INDOOR_SENSOR,
            CONF_FORECAST_HOURS: 24,
            CONF_TRAINING_DAYS: TRAINING_DAYS,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    entry = result["result"]

    await hass.async_block_till_done()  # lets the one-off HistoryStore backfill finish

    # ---- 5. press the real recalculate-weights button --------------------
    button_entity_id = "button.e2e_climate_system_recalculate_weights"
    assert hass.states.get(button_entity_id) is not None
    await hass.services.async_call("button", "press", {"entity_id": button_entity_id}, blocking=True)
    await hass.async_block_till_done()

    # ---- 6. plausibility checks on the resulting entities -----------------
    weights_state = hass.states.get("sensor.e2e_climate_system_weights")
    assert weights_state is not None
    r2 = float(weights_state.state)
    assert 0.0 <= r2 <= 1.0

    coefficients = weights_state.attributes["coefficients"]
    assert coefficients["heating_degree_hours"] > 0
    assert weights_state.attributes["n_samples"] >= len(coefficients) + 5

    forecast_state = hass.states.get("sensor.e2e_climate_system_power_forecast")
    assert forecast_state is not None
    assert float(forecast_state.state) >= 0.0

    forecast_series = forecast_state.attributes["forecast"]
    assert len(forecast_series) == 24
    for entry_row in forecast_series:
        assert entry_row["value"] >= 0.0

    mild_hour_forecast = forecast_series[0]["value"]  # hour+1, 15°C outdoor
    cold_hour_forecast = forecast_series[11]["value"]  # hour+12, -8°C outdoor
    assert cold_hour_forecast > mild_hour_forecast

    await hass.config_entries.async_unload(entry.entry_id)
