"""weather.* entities are read via weather.get_forecasts, not a state attribute."""
from __future__ import annotations

from homeassistant.core import SupportsResponse

from custom_components.haeo_klima_forecast.weather import async_get_forecast_points


async def test_weather_entity_uses_get_forecasts_service(hass):
    hass.states.async_set("weather.home", "sunny", {"wind_speed_unit": "km/h"})

    async def _get_forecasts(call):
        return {
            "weather.home": {
                "forecast": [
                    {"datetime": "2026-09-30T12:00:00+00:00", "temperature": 14.5, "humidity": 60,
                     "wind_speed": 36.0, "wind_bearing": 180},
                    {"datetime": "2026-09-30T13:00:00+00:00"},
                ]
            }
        }

    hass.services.async_register("weather", "get_forecasts", _get_forecasts, supports_response=SupportsResponse.ONLY)

    points = await async_get_forecast_points(hass, "weather.home", 24)

    assert len(points) == 1
    assert points[0].temperature_c == 14.5
    assert points[0].humidity_pct == 60
    assert abs(points[0].wind_speed_ms - 10.0) < 1e-6
    assert points[0].wind_direction_deg == 180


async def test_template_sensor_still_read_via_attribute(hass):
    hass.states.async_set(
        "sensor.fc", "5", {"forecast": [{"time": "2026-09-30T12:00:00+00:00", "value": 7.0}]}
    )
    points = await async_get_forecast_points(hass, "sensor.fc", 24)
    assert [p.temperature_c for p in points] == [7.0]
