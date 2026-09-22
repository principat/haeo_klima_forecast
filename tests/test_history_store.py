"""Tests for the HistoryStore cache and its background sync/sampling jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util

from custom_components.haeo_klima_forecast import history_store as hs
from custom_components.haeo_klima_forecast.history_store import HistoryStore
from custom_components.haeo_klima_forecast.weather.base import WeatherPoint

ENTRY_ID = "history_test_entry"


async def test_merge_and_load_round_trip(hass) -> None:
    store = HistoryStore(hass, ENTRY_ID)
    await store.async_merge({"2026-01-01T00:00:00+00:00": {"power_kw": 1.0}})
    await store.async_merge({"2026-01-01T01:00:00+00:00": {"power_kw": 2.0}})

    rows = await store.async_load()

    assert rows["2026-01-01T00:00:00+00:00"]["power_kw"] == 1.0
    assert rows["2026-01-01T01:00:00+00:00"]["power_kw"] == 2.0


async def test_latest_hour_and_rows_since(hass) -> None:
    store = HistoryStore(hass, ENTRY_ID)
    await store.async_merge(
        {
            "2026-01-01T00:00:00+00:00": {"power_kw": 1.0},
            "2026-01-02T00:00:00+00:00": {"power_kw": 2.0},
        }
    )

    latest = await store.async_latest_hour()
    assert latest == datetime(2026, 1, 2, tzinfo=timezone.utc)

    since = await store.async_rows_since(datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
    assert list(since.keys()) == ["2026-01-02T00:00:00+00:00"]


async def test_merge_caps_stored_hours(hass, monkeypatch) -> None:
    monkeypatch.setattr(hs, "MAX_STORED_HOURS", 2)
    store = HistoryStore(hass, ENTRY_ID)
    await store.async_merge(
        {
            "2026-01-01T00:00:00+00:00": {"power_kw": 1.0},
            "2026-01-01T01:00:00+00:00": {"power_kw": 2.0},
            "2026-01-01T02:00:00+00:00": {"power_kw": 3.0},
        }
    )

    rows = await store.async_load()

    assert len(rows) == 2
    assert "2026-01-01T00:00:00+00:00" not in rows  # oldest evicted


async def test_hourly_sampler_writes_previous_hour_only(hass, freezer) -> None:
    freezer.move_to(datetime(2026, 1, 1, 13, 5, tzinfo=timezone.utc))
    hass.states.async_set("climate.living_room", "heat", {"current_temperature": 21.3})
    store = HistoryStore(hass, ENTRY_ID)

    await hs.async_sample_indoor_temperature_now(hass, store, "climate.living_room", "current_temperature")

    rows = await store.async_load()
    assert rows["2026-01-01T12:00:00+00:00"]["indoor_temp"] == 21.3


async def test_hourly_sampler_ignores_missing_attribute(hass) -> None:
    hass.states.async_set("climate.living_room", "off", {})
    store = HistoryStore(hass, ENTRY_ID)

    await hs.async_sample_indoor_temperature_now(hass, store, "climate.living_room", "current_temperature")

    assert await store.async_load() == {}


async def test_sync_history_backfill_merges_power_indoor_and_weather(hass, monkeypatch, freezer) -> None:
    freezer.move_to(datetime(2026, 1, 3, 5, 0, tzinfo=timezone.utc))
    store = HistoryStore(hass, ENTRY_ID)

    hour0 = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)

    async def fake_statistics(hass_, entity_id, start, end):
        return {hour0: 1.5} if entity_id == "sensor.power" else {hour0: 20.0}

    async def fake_historical(self, start, end):
        return [WeatherPoint(timestamp=hour0, temperature_c=3.0, humidity_pct=80.0)]

    monkeypatch.setattr(hs, "_fetch_statistics_mean_per_hour", fake_statistics)
    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        fake_historical,
    )

    await hs.async_sync_history(
        hass,
        store,
        power_entity_id="sensor.power",
        indoor_temp_entity_id="sensor.indoor_therm",
        indoor_temp_attribute=None,
        latitude=48.0,
        longitude=11.0,
        session=None,
        max_lookback_days=5,
    )

    rows = await store.async_load()
    row = rows[hour0.isoformat()]
    assert row["power_kw"] == 1.5
    assert row["indoor_temp"] == 20.0
    assert row["outdoor_temp"] == 3.0
    assert row["humidity"] == 80.0


async def test_sync_history_raw_fallback_does_not_override_sampled_indoor_temp(hass, monkeypatch, freezer) -> None:
    """First-ever sync for a climate-entity source: raw-state fallback must not clobber
    an hour the hourly sampler already covered when the fallback finds nothing there."""
    freezer.move_to(datetime(2026, 1, 3, 5, 0, tzinfo=timezone.utc))
    store = HistoryStore(hass, ENTRY_ID)

    already_sampled_hour = datetime(2026, 1, 2, 0, tzinfo=timezone.utc)
    await store.async_merge({already_sampled_hour.isoformat(): {"indoor_temp": 22.5}})

    async def fake_statistics(hass_, entity_id, start, end):
        return {already_sampled_hour: 1.1}

    async def fake_raw_hourly_mean(hass_, entity_id, start, end, attribute=None):
        return {}  # no recorder in this test; the sampler already covered the hour

    async def fake_historical(self, start, end):
        return [WeatherPoint(timestamp=already_sampled_hour, temperature_c=4.0)]

    monkeypatch.setattr(hs, "_fetch_statistics_mean_per_hour", fake_statistics)
    monkeypatch.setattr(hs, "_fetch_raw_hourly_mean", fake_raw_hourly_mean)
    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        fake_historical,
    )

    await hs.async_sync_history(
        hass,
        store,
        power_entity_id="sensor.power",
        indoor_temp_entity_id="climate.living_room",
        indoor_temp_attribute="current_temperature",  # climate entity -> raw fallback attempted, sampler wins
        latitude=48.0,
        longitude=11.0,
        session=None,
        max_lookback_days=5,
    )

    rows = await store.async_load()
    row = rows[already_sampled_hour.isoformat()]
    assert row["indoor_temp"] == 22.5  # untouched
    assert row["power_kw"] == 1.1
    assert row["outdoor_temp"] == 4.0


async def test_sync_history_incremental_does_not_query_indoor_temp_for_climate_source(
    hass, monkeypatch, freezer
) -> None:
    """Once a first sync has completed, later runs must rely solely on the hourly sampler."""
    freezer.move_to(datetime(2026, 1, 3, 5, 0, tzinfo=timezone.utc))
    store = HistoryStore(hass, ENTRY_ID)

    already_synced_hour = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
    new_hour = datetime(2026, 1, 2, 0, tzinfo=timezone.utc)
    await store.async_merge(
        {
            already_synced_hour.isoformat(): {"power_kw": 1.0, "indoor_temp": 20.0, "outdoor_temp": 2.0},
            new_hour.isoformat(): {"indoor_temp": 21.0},  # sampler already ran for the new hour
        }
    )

    raw_fallback_calls = []

    async def fake_raw_hourly_mean(hass_, entity_id, start, end, attribute=None):
        raw_fallback_calls.append((start, end))
        return {}

    async def fake_statistics(hass_, entity_id, start, end):
        return {new_hour: 1.4}

    async def fake_historical(self, start, end):
        return [WeatherPoint(timestamp=new_hour, temperature_c=6.0)]

    monkeypatch.setattr(hs, "_fetch_raw_hourly_mean", fake_raw_hourly_mean)
    monkeypatch.setattr(hs, "_fetch_statistics_mean_per_hour", fake_statistics)
    monkeypatch.setattr(
        "custom_components.haeo_klima_forecast.weather.openmeteo.OpenMeteoHistoricalClient.async_get_historical",
        fake_historical,
    )

    await hs.async_sync_history(
        hass,
        store,
        power_entity_id="sensor.power",
        indoor_temp_entity_id="climate.living_room",
        indoor_temp_attribute="current_temperature",
        latitude=48.0,
        longitude=11.0,
        session=None,
        max_lookback_days=5,
    )

    assert raw_fallback_calls == []  # incremental sync must not touch raw history at all
    row = (await store.async_load())[new_hour.isoformat()]
    assert row["indoor_temp"] == 21.0  # kept from the sampler
    assert row["power_kw"] == 1.4
    assert row["outdoor_temp"] == 6.0
