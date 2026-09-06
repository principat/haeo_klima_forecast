"""Factory for weather providers."""
from __future__ import annotations

from ..const import WEATHER_PROVIDER_DWD, WEATHER_PROVIDER_OPENMETEO
from .base import WeatherPoint, WeatherProvider
from .dwd import DwdProvider
from .openmeteo import OpenMeteoProvider

_PROVIDERS = {
    WEATHER_PROVIDER_OPENMETEO: OpenMeteoProvider,
    WEATHER_PROVIDER_DWD: DwdProvider,
}


def get_provider(provider_key: str, latitude: float, longitude: float, session) -> WeatherProvider:
    """Instantiates the matching provider based on the configuration key."""
    cls = _PROVIDERS.get(provider_key, OpenMeteoProvider)
    return cls(latitude, longitude, session)


__all__ = ["WeatherPoint", "WeatherProvider", "get_provider"]
