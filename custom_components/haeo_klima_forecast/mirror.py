"""Naming and lookup helpers for the recorder-mirror sensors.

Home Assistant only computes long-term statistics for `sensor` entities that
carry a `state_class`. Neither `climate` nor `weather` entities qualify - the
numeric values that matter for training (setpoint, current temperature,
outdoor temperature, radiation, ...) live in their attributes, which the
statistics engine never looks at. Their raw state history is therefore only
kept for `recorder.purge_keep_days` (commonly far shorter than the
`training_days` this integration wants to look back).

To fix that, the `sensor` platform (see sensor.py) additionally creates a set
of plain numeric mirror sensors with `state_class: measurement`. These are
recorded by HA like any other sensor: short-term stats every 5 minutes,
long-term statistics every hour, kept indefinitely (not subject to
`purge_keep_days`). `weighting.py` reads history preferentially from these
mirrors and only falls back to raw climate-entity history / the weather
provider's own archive API for time ranges the mirrors do not (yet) cover
(e.g. before the integration was first set up, or before an indoor unit was
added to the config).

Entity IDs for these mirrors are not fixed - HA may deduplicate the object_id
suffix on conflicts. This module therefore only fixes the *unique_id*, and
resolves the current entity_id for it via the entity registry on demand.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

SUFFIX_SETPOINT = "setpoint_temperature"
SUFFIX_CURRENT_TEMPERATURE = "current_temperature"
SUFFIX_ACTIVE = "active"
SUFFIX_HVAC_MODE = "hvac_mode_numeric"
SUFFIX_HVAC_ACTION = "hvac_action_numeric"

CLIMATE_MIRROR_SUFFIXES = (
    SUFFIX_SETPOINT,
    SUFFIX_CURRENT_TEMPERATURE,
    SUFFIX_ACTIVE,
    SUFFIX_HVAC_MODE,
    SUFFIX_HVAC_ACTION,
)

WEATHER_SUFFIX_OUTDOOR_TEMPERATURE = "outdoor_temperature"
WEATHER_SUFFIX_SHORTWAVE_RADIATION = "shortwave_radiation"
WEATHER_SUFFIX_WIND_SPEED = "wind_speed"

WEATHER_MIRROR_SUFFIXES = (
    WEATHER_SUFFIX_OUTDOOR_TEMPERATURE,
    WEATHER_SUFFIX_SHORTWAVE_RADIATION,
    WEATHER_SUFFIX_WIND_SPEED,
)


def climate_mirror_unique_id(climate_entity_id: str, suffix: str) -> str:
    """Stable unique_id for a climate-mirror sensor.

    Deliberately NOT scoped by config-entry-id: it is keyed only by the source
    climate entity_id (and derived value), so that deleting and re-adding this
    integration's config entry - which HA gives a fresh entry_id - does not
    orphan the long-term statistics already recorded for that climate entity.
    Reordering indoor units in the options flow is likewise not affected.

    The one edge case this trades away: the same climate entity used as an
    indoor unit in two different config entries at once would collide. That
    is not a supported setup (one climate entity belongs to one system).
    """
    return f"{DOMAIN}_mirror_{climate_entity_id}_{suffix}"


def weather_mirror_unique_id(entry_id: str, suffix: str) -> str:
    """Stable unique_id for a weather-mirror sensor."""
    return f"{entry_id}_mirror_weather_{suffix}"


def resolve_entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    """Resolves the current entity_id for one of our mirror sensors, if it exists yet."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, unique_id)


def encode_hvac_mode(hvac_mode: str | None) -> float:
    """Numeric code for hvac_mode, so it can be recorded/averaged as a measurement."""
    if hvac_mode == "heat":
        return 1.0
    if hvac_mode == "cool":
        return -1.0
    return 0.0


def encode_hvac_action(hvac_action: str | None) -> float:
    """Numeric code for hvac_action, so it can be recorded/averaged as a measurement."""
    if hvac_action == "heating":
        return 1.0
    if hvac_action == "cooling":
        return -1.0
    return 0.0


def decode_hvac_mode(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0.25:
        return "heat"
    if value < -0.25:
        return "cool"
    return None


def decode_hvac_action(value: float | None) -> str | None:
    if value is None:
        return None
    if value > 0.25:
        return "heating"
    if value < -0.25:
        return "cooling"
    return None
