"""Shared weather data representation.

This integration no longer talks to a weather forecast API itself (see
forecast_template.py for the forecast side); `WeatherPoint` is kept as the
common shape both the Open-Meteo historical client and the forecast-template
parser produce, so `weighting.py` and `forecast.py` stay independent of
where a given hour's weather data actually came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class WeatherPoint:
    """A single hourly weather data point."""

    timestamp: datetime
    temperature_c: float
    shortwave_radiation: float | None = None  # W/m², global radiation / GHI
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    humidity_pct: float | None = None
