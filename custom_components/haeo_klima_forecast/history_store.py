"""HistoryStore: the integration's own hourly training-data cache.

Replaces the previous "mirror sensor" approach (see SPECIFICATION.md,
section 1.6/2.6): instead of re-fetching weather history on every training
run and relying on HA to keep long-term statistics for entities that were
only mirrored from the moment this integration was installed, this module
maintains one small local table of hourly rows per config entry:

    {hour_iso: {"power_kw": ..., "indoor_temp": ..., "outdoor_temp": ...,
                "humidity": ..., "radiation": ..., "wind_speed": ...,
                "wind_direction": ...}}

Populated in two phases:
  1. a one-off backfill (as far back as the power sensor / Open-Meteo allow),
  2. a daily incremental sync that only appends the hours since the last run.

`weighting.py` reads exclusively from this store - no more live recorder
queries or weather API calls at training time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .weather.base import WeatherPoint
from .weather.openmeteo import OpenMeteoHistoricalClient

_LOGGER = logging.getLogger(__name__)

# Hard cap so the JSON file cannot grow unbounded even if training_days is
# raised repeatedly over the years - about 2 years of hourly rows.
MAX_STORED_HOURS = 730 * 24

FIELDS = (
    "power_kw",
    "indoor_temp",
    "outdoor_temp",
    "humidity",
    "radiation",
    "wind_speed",
    "wind_direction",
)


class HistoryStore:
    """Persists the hourly training-data cache for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_history_{entry_id}")
        self._rows: dict[str, dict] | None = None

    async def async_load(self) -> dict[str, dict]:
        if self._rows is None:
            data = await self._store.async_load()
            self._rows = (data or {}).get("rows", {})
        return self._rows

    async def async_save(self) -> None:
        if self._rows is None:
            return
        await self._store.async_save({"rows": self._rows})

    async def async_merge(self, new_rows: dict[str, dict]) -> None:
        rows = await self.async_load()
        rows.update(new_rows)
        if len(rows) > MAX_STORED_HOURS:
            for key in sorted(rows.keys())[: len(rows) - MAX_STORED_HOURS]:
                del rows[key]
        self._rows = rows
        await self.async_save()

    async def async_latest_hour(self) -> datetime | None:
        rows = await self.async_load()
        if not rows:
            return None
        return dt_util.parse_datetime(max(rows.keys()))

    async def async_latest_synced_hour(self) -> datetime | None:
        """Latest hour the daily power/weather sync has actually completed.

        Deliberately distinct from `async_latest_hour()`: the hourly
        indoor-temperature sampler (see `async_sample_indoor_temperature_now`)
        writes partial rows for recent hours independently, which must not
        make the daily sync think those hours are already done.
        """
        rows = await self.async_load()
        synced_keys = [key for key, row in rows.items() if "power_kw" in row]
        if not synced_keys:
            return None
        return dt_util.parse_datetime(max(synced_keys))

    async def async_rows_since(self, start: datetime) -> dict[str, dict]:
        rows = await self.async_load()
        start_iso = start.isoformat()
        return {k: v for k, v in rows.items() if k >= start_iso}


def _hour_key(dt: datetime) -> str:
    return dt.replace(minute=0, second=0, microsecond=0).isoformat()


def _hourly_marks(start: datetime, end: datetime) -> list[datetime]:
    marks = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end:
        marks.append(cur)
        cur += timedelta(hours=1)
    return marks


async def _fetch_statistics_mean_per_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Reads the hourly mean of an entity's HA long-term statistics.

    `statistics_during_period` is a synchronous recorder API and must run
    via the recorder executor, not directly on the event loop.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    def _get():
        return statistics_during_period(hass, start, end, {entity_id}, "hour", None, {"mean"})

    stats = await get_instance(hass).async_add_executor_job(_get)
    rows = stats.get(entity_id, [])
    per_hour: dict[datetime, float] = {}
    for row in rows:
        ts = row["start"] if isinstance(row["start"], datetime) else dt_util.utc_from_timestamp(row["start"])
        mean = row.get("mean")
        if mean is not None:
            per_hour[ts] = mean
    return per_hour


async def _fetch_raw_hourly_mean(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, attribute: str | None = None
) -> dict[datetime, float]:
    """Hourly mean built from raw state history (bounded by `recorder.purge_keep_days`).

    Used as the best-effort backfill for sources without their own
    long-term statistics (e.g. a climate entity's `current_temperature`).
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import state_changes_during_period

    def _get():
        return state_changes_during_period(hass, start, end, entity_id)

    history = await get_instance(hass).async_add_executor_job(_get)
    samples: list[tuple[datetime, float]] = []
    for state in history.get(entity_id, []):
        raw = state.attributes.get(attribute) if attribute else state.state
        try:
            samples.append((state.last_changed, float(raw)))
        except (ValueError, TypeError):
            continue

    marks = _hourly_marks(start, end)
    buckets: dict[datetime, list[float]] = {m: [] for m in marks}
    for ts, value in samples:
        mark = ts.replace(minute=0, second=0, microsecond=0)
        if mark in buckets:
            buckets[mark].append(value)
    return {mark: sum(values) / len(values) for mark, values in buckets.items() if values}


async def async_sync_history(
    hass: HomeAssistant,
    store: HistoryStore,
    *,
    power_entity_id: str,
    indoor_temp_entity_id: str,
    indoor_temp_attribute: str | None,
    latitude: float,
    longitude: float,
    session,
    max_lookback_days: int,
) -> None:
    """Runs the one-off backfill or the daily incremental sync, whichever applies."""
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    latest = await store.async_latest_synced_hour()
    if latest is None:
        start = now - timedelta(days=max_lookback_days)
    else:
        start = latest + timedelta(hours=1)
    end = now - timedelta(hours=1)  # the current hour is not complete yet

    if start > end:
        return

    power_by_hour = await _fetch_statistics_mean_per_hour(hass, power_entity_id, start, end)

    if indoor_temp_attribute is not None:
        if latest is None:
            # Climate entity, first-ever backfill: no long-term statistics
            # available, so this is a best-effort fill from raw state
            # history only (bounded by `recorder.purge_keep_days`). From
            # here on, the hourly self-sampler (see coordinator.py) keeps
            # writing real per-hour values directly into the store, so the
            # incremental sync below does not need to fetch this at all.
            indoor_by_hour = await _fetch_raw_hourly_mean(
                hass, indoor_temp_entity_id, start, end, attribute=indoor_temp_attribute
            )
        else:
            indoor_by_hour = {}
    else:
        indoor_by_hour = await _fetch_statistics_mean_per_hour(hass, indoor_temp_entity_id, start, end)

    weather_client = OpenMeteoHistoricalClient(latitude, longitude, session)
    weather_points = await weather_client.async_get_historical(start, end + timedelta(hours=1))
    weather_by_hour = {p.timestamp.replace(minute=0, second=0, microsecond=0): p for p in weather_points}

    existing_rows = await store.async_load()
    new_rows: dict[str, dict] = {}
    for mark in _hourly_marks(start, end):
        key = _hour_key(mark)
        row = dict(existing_rows.get(key, {}))

        power = power_by_hour.get(mark)
        if power is not None:
            row["power_kw"] = power

        weather: WeatherPoint | None = weather_by_hour.get(mark)
        if weather is not None:
            row["outdoor_temp"] = weather.temperature_c
            row["humidity"] = weather.humidity_pct
            row["radiation"] = weather.shortwave_radiation
            row["wind_speed"] = weather.wind_speed_ms
            row["wind_direction"] = weather.wind_direction_deg

        indoor = indoor_by_hour.get(mark)
        if indoor is not None:
            row["indoor_temp"] = indoor

        # A row only becomes usable training data once the three required
        # fields are present (see weighting.py) - partial rows (e.g. only
        # the hourly-sampled indoor temperature so far) are kept as-is so a
        # later sync can complete them.
        if row:
            new_rows[key] = row

    if new_rows:
        await store.async_merge(new_rows)
        _LOGGER.debug(
            "HAEO Klima Forecast: HistoryStore synced %s hour(s) (%s .. %s)",
            len(new_rows),
            start,
            end,
        )


async def async_sample_indoor_temperature_now(
    hass: HomeAssistant, store: HistoryStore, entity_id: str, attribute: str
) -> None:
    """Hourly self-sampling for an indoor-temperature source without its own long-term statistics.

    Called once per hour (see coordinator.py) for climate-entity indoor
    temperature sources, writing exactly one row for the just-finished hour
    - not one row per attribute change like the old mirror-sensor approach.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return
    try:
        value = float(state.attributes.get(attribute))
    except (TypeError, ValueError):
        return

    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    rows = await store.async_load()
    key = _hour_key(hour)
    existing = rows.get(key, {})
    existing["indoor_temp"] = value
    await store.async_merge({key: existing})
