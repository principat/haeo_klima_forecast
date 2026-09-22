"""Weather data access: historical (Open-Meteo, fixed provider) and forecast (template entity)."""
from __future__ import annotations

from .base import WeatherPoint
from .forecast_template import async_parse_forecast_entity
from .openmeteo import OpenMeteoHistoricalClient

__all__ = ["WeatherPoint", "OpenMeteoHistoricalClient", "async_parse_forecast_entity"]
