"""Abstract interface for weather data providers.

So that the integration is not tied to a fixed weather service, every
provider implements the same interface. New providers (e.g. Bright Sky/DWD,
Meteostat, ...) can be added easily without having to adapt the coordinator,
weighting, or forecast logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class WeatherPoint:
    """A single hourly weather data point."""

    timestamp: datetime
    temperature_c: float
    shortwave_radiation: float | None = None  # W/m², global radiation / GHI
    wind_speed_ms: float | None = None
    humidity_pct: float | None = None


class WeatherProvider(ABC):
    """Base class for weather providers (forecast + history)."""

    name: str = "base"

    def __init__(self, latitude: float, longitude: float, session) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.session = session

    @abstractmethod
    async def async_get_forecast(self, hours: int) -> list[WeatherPoint]:
        """Returns hourly forecast data for the next `hours` hours."""

    @abstractmethod
    async def async_get_historical(
        self, start: datetime, end: datetime
    ) -> list[WeatherPoint]:
        """Returns hourly historical weather data for the period [start, end)."""
