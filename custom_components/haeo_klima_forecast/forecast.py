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

from homeassistant.util import dt as dt_util

from .weather.base import WeatherPoint


@dataclass
class ForecastHour:
    timestamp: datetime
    predicted_kw: float
    heating_degree_hours: float
    cooling_degree_hours: float
    outdoor_temp: float
    indoor_temp: float


TIME_OF_DAY_FEATURES = ["hour_sin", "hour_cos", "hour_sin2", "hour_cos2"]

# Breakpoints (°C) of the piecewise-linear outdoor-temperature terms: the
# unit modulates within a band, so a single slope does not fit. A knot only
# becomes a feature if the training data covers it (see weighting.py).
OUTDOOR_KNOTS = (0, 5, 10, 15, 20, 25, 30)


def knot_feature_name(knot: int) -> str:
    return f"outdoor_above_{knot}"


def feature_values(
    outdoor: float,
    indoor: float,
    hour: int,
    wind_speed: float | None = None,
    humidity: float | None = None,
    wind_direction: float | None = None,
) -> dict[str, float]:
    """All candidate model features for one hour (`hour` = local hour of day).

    Shared by the training (weighting.py) and the forecast, so both always
    build the features the same way. Optional inputs that are missing are
    simply left out.
    """
    values: dict[str, float] = {
        "bias": 1.0,
        "heating_degree_hours": max(0.0, indoor - outdoor),
        "cooling_degree_hours": max(0.0, outdoor - indoor),
        "hour_sin": math.sin(2 * math.pi * hour / 24),
        "hour_cos": math.cos(2 * math.pi * hour / 24),
        "hour_sin2": math.sin(4 * math.pi * hour / 24),
        "hour_cos2": math.cos(4 * math.pi * hour / 24),
    }
    for knot in OUTDOOR_KNOTS:
        values[knot_feature_name(knot)] = max(0.0, outdoor - knot)
    if wind_speed is not None:
        values["wind_speed"] = wind_speed
    if humidity is not None:
        values["humidity"] = humidity
    if wind_direction is not None:
        radians = math.radians(wind_direction)
        values["wind_direction_sin"] = math.sin(radians)
        values["wind_direction_cos"] = math.cos(radians)
    return values


def compute_hour(
    weather: WeatherPoint, indoor_temp: float, coefficients: dict[str, float], max_kw: float | None = None
) -> ForecastHour:
    """Computes the predicted power for a single hour."""
    values = feature_values(
        weather.temperature_c,
        indoor_temp,
        dt_util.as_local(weather.timestamp).hour,
        weather.wind_speed_ms,
        weather.humidity_pct,
        weather.wind_direction_deg,
    )
    heating_degree_hours = values["heating_degree_hours"]
    cooling_degree_hours = values["cooling_degree_hours"]

    # Coefficients of features the model was not trained with are absent -> 0.
    predicted = sum(coefficients.get(name, 0.0) * value for name, value in values.items())

    predicted = max(0.0, predicted)  # negative forecasts make no physical sense
    if max_kw is not None:
        predicted = min(predicted, max_kw)

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
    max_kw: float | None = None,
) -> list[ForecastHour]:
    """Computes the complete hourly forecast series for the forecast horizon.

    `indoor_temps` must be aligned with `weather_points` (same length, same
    order) - see coordinator.py for how the 24h-old indoor temperature is
    resolved for each forecast hour.
    """
    return [
        compute_hour(wp, indoor_temp, coefficients, max_kw)
        for wp, indoor_temp in zip(weather_points, indoor_temps)
    ]
