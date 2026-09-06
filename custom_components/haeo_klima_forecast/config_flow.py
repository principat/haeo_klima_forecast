"""Config and options flow: complete setup via the HA UI."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DUTY_THROTTLE_ENABLED,
    CONF_DUTY_THROTTLE_LOAD_ENTITY,
    CONF_DUTY_THROTTLE_MAX_OFFSET,
    CONF_DUTY_THROTTLE_OFFSET_ENTITY,
    CONF_DUTY_THROTTLE_STEP,
    CONF_DUTY_THROTTLE_THRESHOLD,
    CONF_ENERGY_SENSOR,
    CONF_FORECAST_HOURS,
    CONF_INDOOR_UNITS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_NIGHT_SETBACK_ACTIVE_ENTITY,
    CONF_NIGHT_SETBACK_ENABLED,
    CONF_NIGHT_SETBACK_END,
    CONF_NIGHT_SETBACK_OFFSET_ENTITY,
    CONF_NIGHT_SETBACK_START,
    CONF_POWER_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_UPDATE_INTERVAL_MIN,
    CONF_WEATHER_PROVIDER,
    DEFAULT_FORECAST_HOURS,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    IU_CLIMATE_ENTITY,
    IU_NAME,
    WEATHER_PROVIDERS,
)

MAX_INDOOR_UNITS = 12


def _general_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Climate system")): str,
            vol.Required(CONF_ENERGY_SENSOR, default=defaults.get(CONF_ENERGY_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
            vol.Required(CONF_POWER_SENSOR, default=defaults.get(CONF_POWER_SENSOR)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(
                CONF_WEATHER_PROVIDER, default=defaults.get(CONF_WEATHER_PROVIDER, WEATHER_PROVIDERS[0])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=WEATHER_PROVIDERS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
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
            vol.Required("indoor_unit_count", default=len(defaults.get(CONF_INDOOR_UNITS, [1]) or [1]) or 1): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_INDOOR_UNITS)
            ),
        }
    )


def _indoor_unit_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(IU_NAME, default=defaults.get(IU_NAME, "")): str,
            vol.Required(IU_CLIMATE_ENTITY, default=defaults.get(IU_CLIMATE_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
        }
    )


def _night_setback_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NIGHT_SETBACK_ENABLED, default=defaults.get(CONF_NIGHT_SETBACK_ENABLED, False)): bool,
            vol.Optional(
                CONF_NIGHT_SETBACK_ACTIVE_ENTITY, default=defaults.get(CONF_NIGHT_SETBACK_ACTIVE_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_boolean", "binary_sensor"])),
            vol.Optional(
                CONF_NIGHT_SETBACK_OFFSET_ENTITY, default=defaults.get(CONF_NIGHT_SETBACK_OFFSET_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_number", "number"])),
            vol.Optional(
                CONF_NIGHT_SETBACK_START, default=defaults.get(CONF_NIGHT_SETBACK_START, "22:00")
            ): str,
            vol.Optional(CONF_NIGHT_SETBACK_END, default=defaults.get(CONF_NIGHT_SETBACK_END, "06:00")): str,
        }
    )


def _duty_throttle_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_DUTY_THROTTLE_ENABLED, default=defaults.get(CONF_DUTY_THROTTLE_ENABLED, False)
            ): bool,
            vol.Optional(
                CONF_DUTY_THROTTLE_LOAD_ENTITY, default=defaults.get(CONF_DUTY_THROTTLE_LOAD_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_DUTY_THROTTLE_OFFSET_ENTITY, default=defaults.get(CONF_DUTY_THROTTLE_OFFSET_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain=["input_number", "number"])),
            vol.Optional(
                CONF_DUTY_THROTTLE_THRESHOLD, default=defaults.get(CONF_DUTY_THROTTLE_THRESHOLD, 300)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_DUTY_THROTTLE_MAX_OFFSET, default=defaults.get(CONF_DUTY_THROTTLE_MAX_OFFSET, 2.0)
            ): vol.Coerce(float),
            vol.Optional(
                CONF_DUTY_THROTTLE_STEP, default=defaults.get(CONF_DUTY_THROTTLE_STEP, 0.5)
            ): vol.Coerce(float),
        }
    )


class HaeoKlimaForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup of a climate system (multiple systems = multiple entries)."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._unit_count = 1
        self._unit_index = 0
        self._units: list[dict] = []

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._unit_count = user_input.pop("indoor_unit_count")
            if not user_input.get(CONF_LATITUDE):
                user_input[CONF_LATITUDE] = self.hass.config.latitude
            if not user_input.get(CONF_LONGITUDE):
                user_input[CONF_LONGITUDE] = self.hass.config.longitude
            self._data.update(user_input)
            self._unit_index = 0
            self._units = []
            return await self.async_step_indoor_unit()

        return self.async_show_form(step_id="user", data_schema=_general_schema({}), errors=errors)

    async def async_step_indoor_unit(self, user_input: dict | None = None):
        if user_input is not None:
            self._units.append(user_input)
            self._unit_index += 1

        if self._unit_index >= self._unit_count:
            self._data[CONF_INDOOR_UNITS] = self._units
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        return self.async_show_form(
            step_id="indoor_unit",
            data_schema=_indoor_unit_schema({}),
            description_placeholders={"index": str(self._unit_index + 1), "count": str(self._unit_count)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: config_entries.ConfigEntry):
        return HaeoKlimaForecastOptionsFlow()


class HaeoKlimaForecastOptionsFlow(config_entries.OptionsFlow):
    """Subsequent adjustment: general settings, indoor units, special adjustments."""

    def __init__(self) -> None:
        self._current: dict[str, Any] | None = None
        self._unit_count = 1
        self._unit_index = 0
        self._units: list[dict] = []
        self._pending: dict[str, Any] = {}

    @property
    def current(self) -> dict[str, Any]:
        """Return current entry data merged with pending option changes."""
        if self._current is None:
            self._current = {**self.config_entry.data, **self.config_entry.options}
        return {**self._current, **self._pending}

    async def async_step_init(self, user_input: dict | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "indoor_units", "night_setback", "duty_throttle"],
        )

    async def async_step_general(self, user_input: dict | None = None):
        if user_input is not None:
            user_input.pop("indoor_unit_count", None)
            self._pending.update(user_input)
            return self._save()
        schema = _general_schema(self.current)
        # The indoor unit count is not saved here, that's handled by "indoor_units"
        schema = vol.Schema({k: v for k, v in schema.schema.items() if str(k) != "indoor_unit_count"})
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_indoor_units(self, user_input: dict | None = None):
        if user_input is not None:
            self._unit_count = user_input["indoor_unit_count"]
            self._unit_index = 0
            self._units = []
            return await self.async_step_indoor_unit_edit()
        current_count = len(self.current.get(CONF_INDOOR_UNITS, [])) or 1
        return self.async_show_form(
            step_id="indoor_units",
            data_schema=vol.Schema(
                {
                    vol.Required("indoor_unit_count", default=current_count): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=MAX_INDOOR_UNITS)
                    )
                }
            ),
        )

    async def async_step_indoor_unit_edit(self, user_input: dict | None = None):
        if user_input is not None:
            self._units.append(user_input)
            self._unit_index += 1

        if self._unit_index >= self._unit_count:
            self._pending[CONF_INDOOR_UNITS] = self._units
            return self._save()

        existing_units = self.current.get(CONF_INDOOR_UNITS, [])
        defaults = existing_units[self._unit_index] if self._unit_index < len(existing_units) else {}
        return self.async_show_form(
            step_id="indoor_unit_edit",
            data_schema=_indoor_unit_schema(defaults),
            description_placeholders={"index": str(self._unit_index + 1), "count": str(self._unit_count)},
        )

    async def async_step_night_setback(self, user_input: dict | None = None):
        if user_input is not None:
            self._pending.update(user_input)
            return self._save()
        return self.async_show_form(step_id="night_setback", data_schema=_night_setback_schema(self.current))

    async def async_step_duty_throttle(self, user_input: dict | None = None):
        if user_input is not None:
            self._pending.update(user_input)
            return self._save()
        return self.async_show_form(step_id="duty_throttle", data_schema=_duty_throttle_schema(self.current))

    def _save(self):
        new_options = {**self.config_entry.options, **self._pending}
        return self.async_create_entry(title="", data=new_options)
