"""Unit tests for the pure forecast formula (no hass instance needed)."""
from __future__ import annotations

from datetime import datetime, timezone

from custom_components.haeo_klima_forecast.forecast import compute_forecast_series, compute_hour
from custom_components.haeo_klima_forecast.weather.base import WeatherPoint

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def test_heating_degree_hours_when_colder_outside() -> None:
    weather = WeatherPoint(timestamp=NOW, temperature_c=-2.0)
    result = compute_hour(weather, indoor_temp=20.0, coefficients={"bias": 1.0, "heating_degree_hours": 0.1})

    assert result.heating_degree_hours == 22.0
    assert result.cooling_degree_hours == 0.0
    assert result.predicted_kw == 1.0 + 0.1 * 22.0


def test_cooling_degree_hours_when_hotter_outside() -> None:
    weather = WeatherPoint(timestamp=NOW, temperature_c=32.0)
    result = compute_hour(weather, indoor_temp=24.0, coefficients={"bias": 0.5, "cooling_degree_hours": 0.2})

    assert result.cooling_degree_hours == 8.0
    assert result.heating_degree_hours == 0.0
    assert result.predicted_kw == 0.5 + 0.2 * 8.0


def test_optional_features_only_applied_when_present_in_weather_point() -> None:
    coefficients = {
        "bias": 0.0,
        "shortwave_radiation": 0.01,
        "wind_speed": 0.05,
        "humidity": 0.001,
    }
    with_extras = WeatherPoint(
        timestamp=NOW, temperature_c=20.0, shortwave_radiation=100.0, wind_speed_ms=4.0, humidity_pct=60.0
    )
    without_extras = WeatherPoint(timestamp=NOW, temperature_c=20.0)

    result_with = compute_hour(with_extras, indoor_temp=20.0, coefficients=coefficients)
    result_without = compute_hour(without_extras, indoor_temp=20.0, coefficients=coefficients)

    assert result_with.predicted_kw == 0.01 * 100.0 + 0.05 * 4.0 + 0.001 * 60.0
    assert result_without.predicted_kw == 0.0


def test_wind_direction_uses_circular_sin_cos_encoding() -> None:
    coefficients = {"bias": 0.0, "wind_direction_sin": 1.0, "wind_direction_cos": 0.0}
    east = WeatherPoint(timestamp=NOW, temperature_c=20.0, wind_direction_deg=90.0)

    result = compute_hour(east, indoor_temp=20.0, coefficients=coefficients)

    assert result.predicted_kw == 1.0  # sin(90°) == 1


def test_negative_prediction_is_clamped_to_zero() -> None:
    weather = WeatherPoint(timestamp=NOW, temperature_c=20.0)
    result = compute_hour(weather, indoor_temp=20.0, coefficients={"bias": -5.0})

    assert result.predicted_kw == 0.0


def test_compute_forecast_series_zips_weather_and_indoor_temps() -> None:
    weather_points = [
        WeatherPoint(timestamp=NOW, temperature_c=0.0),
        WeatherPoint(timestamp=NOW, temperature_c=5.0),
    ]
    indoor_temps = [20.0, 21.0]

    series = compute_forecast_series(weather_points, indoor_temps, {"bias": 0.0, "heating_degree_hours": 1.0})

    assert [f.heating_degree_hours for f in series] == [20.0, 16.0]
