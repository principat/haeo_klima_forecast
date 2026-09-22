"""Coordinator: reads the forecast-template entity, applies the weights, computes the forecast.

Also owns the two background jobs that keep the HistoryStore up to date (see
history_store.py): a daily backfill/incremental sync, and - only if the
indoor-temperature source is a climate entity without its own long-term
statistics - an hourly self-sampler.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_FORECAST_HOURS,
    CONF_INDOOR_TEMP_SOURCE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_POWER_SENSOR,
    CONF_TRAINING_DAYS,
    CONF_UPDATE_INTERVAL_MIN,
    CONF_WEATHER_FORECAST_ENTITY,
    DEFAULT_FORECAST_HOURS,
    DEFAULT_TRAINING_DAYS,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    HISTORY_SYNC_HOUR,
    HISTORY_SYNC_MINUTE,
)
from .forecast import compute_forecast_series
from .history_store import HistoryStore, async_sample_indoor_temperature_now, async_sync_history
from .weather.forecast_template import async_parse_forecast_entity
from .weighting import WeightStore, async_train_weights

_LOGGER = logging.getLogger(__name__)

CLIMATE_CURRENT_TEMP_ATTRIBUTE = "current_temperature"


def _indoor_temp_source(config: dict) -> tuple[str | None, str | None]:
    """Returns (entity_id, attribute). `attribute` is set only for climate entities."""
    entity_id = config.get(CONF_INDOOR_TEMP_SOURCE)
    if entity_id is None:
        return None, None
    if entity_id.split(".", 1)[0] == "climate":
        return entity_id, CLIMATE_CURRENT_TEMP_ATTRIBUTE
    return entity_id, None


class HaeoForecastCoordinator(DataUpdateCoordinator):
    """Periodically computes the power forecast for a climate system."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.entry = entry
        self.config = {**entry.data, **entry.options}
        self.weight_store = WeightStore(hass, entry.entry_id)
        self.history_store = HistoryStore(hass, entry.entry_id)
        self._weights_cache: dict | None = None
        self._unsub_history_sync: CALLBACK_TYPE | None = None
        self._unsub_hourly_sample: CALLBACK_TYPE | None = None

        update_minutes = self.config.get(CONF_UPDATE_INTERVAL_MIN, DEFAULT_UPDATE_INTERVAL_MIN)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=update_minutes),
        )

    def async_start_background_jobs(self) -> None:
        """Starts the daily HistoryStore sync and, if needed, the hourly indoor-temp sampler.

        A no-op if this config entry is missing one of the required data
        sources (e.g. an incompletely set up / test entry) - defensive, so a
        partial configuration cannot trigger unwanted network calls.
        """
        entity_id, attribute = _indoor_temp_source(self.config)
        if entity_id is None or CONF_POWER_SENSOR not in self.config or CONF_WEATHER_FORECAST_ENTITY not in self.config:
            return

        self._unsub_history_sync = async_track_time_change(
            self.hass, self._async_on_history_sync_time, hour=HISTORY_SYNC_HOUR, minute=HISTORY_SYNC_MINUTE, second=0
        )
        if attribute is not None:
            self._unsub_hourly_sample = async_track_time_interval(
                self.hass, self._async_on_hourly_sample, timedelta(hours=1)
            )
        self.hass.async_create_task(self._async_sync_history())

    def async_stop_background_jobs(self) -> None:
        if self._unsub_history_sync is not None:
            self._unsub_history_sync()
            self._unsub_history_sync = None
        if self._unsub_hourly_sample is not None:
            self._unsub_hourly_sample()
            self._unsub_hourly_sample = None

    async def _async_on_history_sync_time(self, _now) -> None:
        await self._async_sync_history()

    @callback
    def _async_on_hourly_sample(self, _now) -> None:
        entity_id, attribute = _indoor_temp_source(self.config)
        self.hass.async_create_task(
            async_sample_indoor_temperature_now(self.hass, self.history_store, entity_id, attribute)
        )

    async def _async_sync_history(self) -> None:
        power_entity_id = self.config[CONF_POWER_SENSOR]
        indoor_entity_id, indoor_attribute = _indoor_temp_source(self.config)
        lat = self.config.get(CONF_LATITUDE, self.hass.config.latitude)
        lon = self.config.get(CONF_LONGITUDE, self.hass.config.longitude)
        training_days = self.config.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS)

        session = aiohttp.ClientSession()
        try:
            await async_sync_history(
                self.hass,
                self.history_store,
                power_entity_id=power_entity_id,
                indoor_temp_entity_id=indoor_entity_id,
                indoor_temp_attribute=indoor_attribute,
                latitude=lat,
                longitude=lon,
                session=session,
                max_lookback_days=training_days,
            )
        except Exception:  # noqa: BLE001 - a failed sync must not crash HA, just retry next time
            _LOGGER.exception("HAEO Klima Forecast: HistoryStore sync failed")
        finally:
            await session.close()

    async def _async_update_data(self) -> dict:
        weights = self._weights_cache or await self.weight_store.async_load()
        if not weights:
            raise UpdateFailed(
                "No weights have been calculated yet. Please run the service "
                f"'{DOMAIN}.recalculate_weights' once, as soon as enough "
                "historical data is available."
            )
        self._weights_cache = weights

        hours = self.config.get(CONF_FORECAST_HOURS, DEFAULT_FORECAST_HOURS)
        weather_points = async_parse_forecast_entity(
            self.hass, self.config[CONF_WEATHER_FORECAST_ENTITY], hours
        )

        indoor_temps = await self._async_resolve_indoor_temps(weather_points)

        forecast = compute_forecast_series(weather_points, indoor_temps, weights["coefficients"])

        return {
            "forecast": [
                {
                    "time": f.timestamp.isoformat(),
                    "value": round(f.predicted_kw, 4),
                    "outdoor_temp": f.outdoor_temp,
                    "indoor_temp": f.indoor_temp,
                    "heating_degree_hours": round(f.heating_degree_hours, 3),
                    "cooling_degree_hours": round(f.cooling_degree_hours, 3),
                }
                for f in forecast
            ],
            "weights": weights,
        }

    async def _async_resolve_indoor_temps(self, weather_points) -> list[float]:
        """Resolves the "indoor temperature 24h ago, cyclically" input for each forecast hour."""
        rows = await self.history_store.async_load()
        fallback = self._current_indoor_temp_fallback()

        result: list[float] = []
        for wp in weather_points:
            hour = wp.timestamp.replace(minute=0, second=0, microsecond=0)
            key = (hour - timedelta(hours=24)).isoformat()
            value = rows.get(key, {}).get("indoor_temp")
            result.append(value if value is not None else fallback)
        return result

    def _current_indoor_temp_fallback(self) -> float:
        """Best-effort fallback for hours the HistoryStore does not cover yet."""
        entity_id, attribute = _indoor_temp_source(self.config)
        state = self.hass.states.get(entity_id)
        if state is None:
            return 21.0
        try:
            raw = state.attributes.get(attribute) if attribute else state.state
            return float(raw)
        except (TypeError, ValueError):
            return 21.0

    async def async_recalculate_weights(self) -> None:
        """Called by the `haeo_klima_forecast.recalculate_weights` service."""
        training_days = self.config.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS)
        result = await async_train_weights(self.hass, self.entry.entry_id, self.config, training_days)
        await self.weight_store.async_save(result)
        self._weights_cache = {
            "coefficients": result.coefficients,
            "r2": result.r2,
            "n_samples": result.n_samples,
            "trained_at": result.trained_at,
            "feature_names": result.feature_names,
        }
        await self.async_request_refresh()
