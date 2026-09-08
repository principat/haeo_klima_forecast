"""Coordinator: fetches the weather forecast, reads current states and computes the forecast."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DUTY_THROTTLE_ENABLED,
    CONF_DUTY_THROTTLE_OFFSET_ENTITY,
    CONF_FORECAST_HOURS,
    CONF_INDOOR_UNITS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NIGHT_SETBACK_ENABLED,
    CONF_NIGHT_SETBACK_END,
    CONF_NIGHT_SETBACK_OFFSET_ENTITY,
    CONF_NIGHT_SETBACK_START,
    CONF_UPDATE_INTERVAL_MIN,
    CONF_WEATHER_PROVIDER,
    DEFAULT_FORECAST_HOURS,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    IU_CLIMATE_ENTITY,
    IU_NAME,
)
from .forecast import IndoorUnitPlan, compute_forecast_series
from .weather import get_provider
from .weighting import WeightStore, async_train_weights

_LOGGER = logging.getLogger(__name__)


class HaeoForecastCoordinator(DataUpdateCoordinator):
    """Periodically computes the power forecast for a climate system."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.weight_store = WeightStore(hass, entry.entry_id)
        self._weights_cache: dict | None = None
        self.latest_weather_point = None

        update_minutes = self.config.get(CONF_UPDATE_INTERVAL_MIN, DEFAULT_UPDATE_INTERVAL_MIN)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_minutes),
        )

    def _get_provider(self):
        session = aiohttp.ClientSession()  # see note in README: ideally use
        # async_get_clientsession(self.hass) from homeassistant.helpers.aiohttp_client;
        # kept explicit here so the provider class stays HA-independent.
        lat = self.config.get(CONF_LATITUDE, self.hass.config.latitude)
        lon = self.config.get(CONF_LONGITUDE, self.hass.config.longitude)
        return get_provider(self.config.get(CONF_WEATHER_PROVIDER, "openmeteo"), lat, lon, session), session

    async def _async_update_data(self) -> dict:
        weights = self._weights_cache or await self.weight_store.async_load()
        if not weights:
            raise UpdateFailed(
                "No weights have been calculated yet. Please run the service "
                f"'{DOMAIN}.recalculate_weights' once, as soon as enough "
                "historical data is available."
            )
        self._weights_cache = weights

        provider, session = self._get_provider()
        try:
            hours = self.config.get(CONF_FORECAST_HOURS, DEFAULT_FORECAST_HOURS)
            weather_points = await provider.async_get_forecast(hours)
        finally:
            await session.close()

        if weather_points:
            # Also feeds the weather-mirror sensors (see mirror.py / sensor.py):
            # the first forecast point is "now", which is what gets recorded so
            # that future training can rely on our own long-term statistics
            # instead of only the provider's historical archive API.
            self.latest_weather_point = weather_points[0]

        units = self._collect_indoor_unit_plans()
        night_cfg = self._night_setback_config()

        forecast = compute_forecast_series(
            weather_points,
            units,
            weights["coefficients"],
            night_cfg,
            duty_throttle_assumed_offset=0.0,
        )

        return {
            "forecast": [
                {
                    "time": f.timestamp.isoformat(),
                    "value": round(f.predicted_kw, 4),
                    "outdoor_temp": f.outdoor_temp,
                    "degree_hours": round(f.degree_hours, 3),
                    "heating_degree_hours": round(f.heating_degree_hours, 3),
                    "cooling_degree_hours": round(f.cooling_degree_hours, 3),
                    "night_setback_offset": f.night_setback_offset,
                    "duty_throttle_offset": f.duty_throttle_offset,
                }
                for f in forecast
            ],
            "weights": weights,
        }

    def _collect_indoor_unit_plans(self) -> list[IndoorUnitPlan]:
        plans: list[IndoorUnitPlan] = []
        for unit in self.config.get(CONF_INDOOR_UNITS, []):
            entity_id = unit.get(IU_CLIMATE_ENTITY)
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None:
                continue
            setpoint = state.attributes.get("temperature")
            setpoint_value = _try_float(setpoint)
            current_temp = state.attributes.get("current_temperature")
            hvac_mode = state.state
            is_active = hvac_mode not in ("off", "unavailable", "unknown")
            resolved_mode = _resolve_hvac_mode(
                hvac_mode,
                state.attributes.get("hvac_action"),
                _try_float(current_temp),
                setpoint,
            )
            plans.append(
                IndoorUnitPlan(
                    name=unit.get(IU_NAME, entity_id),
                    base_setpoint=setpoint_value if setpoint_value is not None else 21.0,
                    hvac_mode=resolved_mode,
                    is_active=is_active,
                )
            )
        return plans

    def _night_setback_config(self) -> dict | None:
        if not self.config.get(CONF_NIGHT_SETBACK_ENABLED):
            return None
        offset_entity = self.config.get(CONF_NIGHT_SETBACK_OFFSET_ENTITY)
        offset_state = self.hass.states.get(offset_entity) if offset_entity else None
        try:
            offset_value = float(offset_state.state) if offset_state else 2.0
        except (ValueError, TypeError):
            offset_value = 2.0
        return {
            "enabled": True,
            "start": self.config.get(CONF_NIGHT_SETBACK_START, "22:00"),
            "end": self.config.get(CONF_NIGHT_SETBACK_END, "06:00"),
            "offset": offset_value,
        }

    async def async_recalculate_weights(self) -> None:
        """Called by the `haeo_klima_forecast.recalculate_weights` service."""
        provider, session = self._get_provider()
        try:
            result = await async_train_weights(
                self.hass, self.entry.entry_id, self.config, provider
            )
        finally:
            await session.close()
        await self.weight_store.async_save(result)
        self._weights_cache = {
            "coefficients": result.coefficients,
            "r2": result.r2,
            "n_samples": result.n_samples,
            "trained_at": result.trained_at,
            "feature_names": result.feature_names,
        }
        await self.async_request_refresh()


def _resolve_hvac_mode(
    hvac_mode: str | None,
    hvac_action: str | None,
    current_temp: float | None,
    setpoint: float | None,
) -> str:
    """Resolve a climate state to the load direction used by the forecast."""
    if hvac_action == "cooling":
        return "cool"
    if hvac_action == "heating":
        return "heat"
    if hvac_mode == "cool":
        return "cool"
    if hvac_mode == "heat":
        return "heat"
    if current_temp is not None and setpoint is not None:
        setpoint_value = _try_float(setpoint)
        if setpoint_value is not None:
            return "cool" if current_temp > setpoint_value else "heat"
    return "heat"


def _try_float(value) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
