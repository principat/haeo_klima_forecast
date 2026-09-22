"""Computation of the power forecast (energy-signature / degree-hours model).

Deliberately kept as pure, simple functions (no I/O, no HA objects): the
coordinator collects the input data (weather forecast, the indoor
temperature from 24 hours ago, the trained weights) and passes it here for
calculation. This keeps the core logic easily testable and independent of
Home Assistant.

Night setback and duty throttling are not modeled explicitly here: the
`indoor_temp` passed in for each hour is the actual measured value from 24
hours ago (see coordinator.py / SPECIFICATION.md section 1.2/1.4), which
already reflects both mechanisms whenever they follow a daily pattern.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .weather.base import WeatherPoint


@dataclass
class ForecastHour:
    timestamp: datetime
    predicted_kw: float
    heating_degree_hours: float
    cooling_degree_hours: float
    outdoor_temp: float
    indoor_temp: float


def compute_hour(weather: WeatherPoint, indoor_temp: float, coefficients: dict[str, float]) -> ForecastHour:
    """Computes the predicted power for a single hour."""
    heating_degree_hours = max(0.0, indoor_temp - weather.temperature_c)
    cooling_degree_hours = max(0.0, weather.temperature_c - indoor_temp)

    predicted = (
        coefficients.get("bias", 0.0)
        + coefficients.get("heating_degree_hours", 0.0) * heating_degree_hours
        + coefficients.get("cooling_degree_hours", 0.0) * cooling_degree_hours
    )

    if weather.shortwave_radiation is not None:
        predicted += coefficients.get("shortwave_radiation", 0.0) * weather.shortwave_radiation
    if weather.wind_speed_ms is not None:
        predicted += coefficients.get("wind_speed", 0.0) * weather.wind_speed_ms
    if weather.humidity_pct is not None:
        predicted += coefficients.get("humidity", 0.0) * weather.humidity_pct
    if weather.wind_direction_deg is not None:
        radians = math.radians(weather.wind_direction_deg)
        predicted += coefficients.get("wind_direction_sin", 0.0) * math.sin(radians)
        predicted += coefficients.get("wind_direction_cos", 0.0) * math.cos(radians)

    predicted = max(0.0, predicted)  # negative forecasts make no physical sense

    return ForecastHour(
        timestamp=weather.timestamp,
        predicted_kw=predicted,
        heating_degree_hours=heating_degree_hours,
        cooling_degree_hours=cooling_degree_hours,
        outdoor_temp=weather.temperature_c,
        indoor_temp=indoor_temp,
    )


def compute_forecast_series(
    weather_points: list[WeatherPoint],
    indoor_temps: list[float],
    coefficients: dict[str, float],
) -> list[ForecastHour]:
    """Computes the complete hourly forecast series for the forecast horizon.

    `indoor_temps` must be aligned with `weather_points` (same length, same
    order) - see coordinator.py for how the 24h-old indoor temperature is
    resolved for each forecast hour.
    """
    return [
        compute_hour(wp, indoor_temp, coefficients)
        for wp, indoor_temp in zip(weather_points, indoor_temps)
    ]
