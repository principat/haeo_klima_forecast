"""Config and options flow: single-step setup via the HA UI.

Deliberately minimal (see SPECIFICATION.md section 1.5): no indoor-unit
list, no night-setback/duty-throttle configuration - just the three data
sources plus a handful of numeric defaults.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_FORECAST_HOURS,
    CONF_INDOOR_TEMP_SOURCE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_POWER_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_UPDATE_INTERVAL_MIN,
    CONF_WEATHER_FORECAST_ENTITY,
    DEFAULT_FORECAST_HOURS,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
)


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Climate system")): str,
            vol.Required(CONF_POWER_SENSOR, default=defaults.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(
                CONF_WEATHER_FORECAST_ENTITY, default=defaults.get(CONF_WEATHER_FORECAST_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_INDOOR_TEMP_SOURCE, default=defaults.get(CONF_INDOOR_TEMP_SOURCE)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["sensor", "climate"])),
            vol.Optional(CONF_LATITUDE, default=defaults.get(CONF_LATITUDE, 0.0)): vol.Coerce(float),
            vol.Optional(CONF_LONGITUDE, default=defaults.get(CONF_LONGITUDE, 0.0)): vol.Coerce(float),
            vol.Optional(
                CONF_FORECAST_HOURS, default=defaults.get(CONF_FORECAST_HOURS, DEFAULT_FORECAST_HOURS)
            ): vol.All(vol.Coerce(int), vol.Range(min=6, max=168)),
            vol.Optional(
                CONF_UPDATE_INTERVAL_MIN,
                default=defaults.get(CONF_UPDATE_INTERVAL_MIN, DEFAULT_UPDATE_INTERVAL_MIN),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=360)),
            vol.Optional(
                CONF_TRAINING_DAYS, default=defaults.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS)
            ): vol.All(vol.Coerce(int), vol.Range(min=14, max=730)),
        }
    )


class HaeoKlimaForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Single-step setup of a climate system (multiple systems = multiple entries)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_LATITUDE):
                user_input[CONF_LATITUDE] = self.hass.config.latitude
            if not user_input.get(CONF_LONGITUDE):
                user_input[CONF_LONGITUDE] = self.hass.config.longitude
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: config_entries.ConfigEntry):
        return HaeoKlimaForecastOptionsFlow()


class HaeoKlimaForecastOptionsFlow(config_entries.OptionsFlow):
    """Subsequent adjustment: same single form, prefilled with the current values."""

    async def async_step_init(self, user_input: dict | None = None):
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=_schema(current))
