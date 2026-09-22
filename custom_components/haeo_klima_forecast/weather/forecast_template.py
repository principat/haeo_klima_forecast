"""Parses a "forecast-template" weather entity into `WeatherPoint`s.

This integration does not talk to any weather forecast API itself. Instead
it consumes an existing HA entity that already exposes a weather forecast in
a small, provider-independent shape:

    state: 5.3   # current outdoor temperature (°C)
    attributes:
      forecast:
        - time: "2026-09-23T14:00:00+00:00"
          value: 6.1        # outdoor temperature (°C), required
          humidity: 72      # %, optional
          radiation: 210    # W/m², optional
          wind_speed: 3.4   # m/s, optional
          wind_direction: 180  # °, optional
        - ...

Turning a concrete weather service's native format into this shape is the
job of a separate, provider-specific "mapper-helper" project, not of this
integration (see SPECIFICATION.md, section 1.7).
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ..const import (
    FORECAST_ATTR_HUMIDITY,
    FORECAST_ATTR_LIST,
    FORECAST_ATTR_RADIATION,
    FORECAST_ATTR_TEMPERATURE,
    FORECAST_ATTR_TIME,
    FORECAST_ATTR_WIND_DIRECTION,
    FORECAST_ATTR_WIND_SPEED,
)
from .base import WeatherPoint

_LOGGER = logging.getLogger(__name__)


def async_parse_forecast_entity(hass: HomeAssistant, entity_id: str, hours: int) -> list[WeatherPoint]:
    """Reads and parses the `forecast` attribute of a forecast-template entity.

    Returns an empty list (with a warning logged) if the entity does not
    exist yet or does not carry a `forecast` attribute - that must not crash
    the coordinator update, just leave this cycle's forecast empty.
    """
    state = hass.states.get(entity_id)
    if state is None:
        _LOGGER.warning(
            "HAEO Klima Forecast: weather forecast entity '%s' not found", entity_id
        )
        return []

    raw_forecast = state.attributes.get(FORECAST_ATTR_LIST)
    if not raw_forecast:
        _LOGGER.warning(
            "HAEO Klima Forecast: weather forecast entity '%s' has no '%s' attribute",
            entity_id,
            FORECAST_ATTR_LIST,
        )
        return []

    points: list[WeatherPoint] = []
    for entry in raw_forecast[:hours]:
        timestamp = _parse_time(entry.get(FORECAST_ATTR_TIME))
        temperature = entry.get(FORECAST_ATTR_TEMPERATURE)
        if timestamp is None or temperature is None:
            continue
        points.append(
            WeatherPoint(
                timestamp=timestamp,
                temperature_c=float(temperature),
                humidity_pct=_try_float(entry.get(FORECAST_ATTR_HUMIDITY)),
                shortwave_radiation=_try_float(entry.get(FORECAST_ATTR_RADIATION)),
                wind_speed_ms=_try_float(entry.get(FORECAST_ATTR_WIND_SPEED)),
                wind_direction_deg=_try_float(entry.get(FORECAST_ATTR_WIND_DIRECTION)),
            )
        )
    return points


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return dt_util.parse_datetime(str(value))
    except (ValueError, TypeError):
        return None


def _try_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None
