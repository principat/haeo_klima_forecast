"""Tests for the training/regression logic in weighting.py."""
from __future__ import annotations

import pytest

from custom_components.haeo_klima_forecast.history_store import HistoryStore
from custom_components.haeo_klima_forecast.weighting import (
    BASE_FEATURE_NAMES,
    _row_to_features,
    _select_optional_features,
    async_train_weights,
)


def test_select_optional_features_requires_minimum_coverage() -> None:
    rows = [{"radiation": 100.0}, {"radiation": 110.0}, {}, {}]  # 50% coverage -> included
    assert _select_optional_features(rows) == ["shortwave_radiation"]

    sparse_rows = [{"wind_speed": 3.0}, {}, {}, {}]  # 25% coverage -> excluded
    assert _select_optional_features(sparse_rows) == []


def test_select_optional_features_wind_direction_adds_two_columns() -> None:
    rows = [{"wind_direction": 90.0}, {"wind_direction": 180.0}]
    assert _select_optional_features(rows) == ["wind_direction_sin", "wind_direction_cos"]


def test_row_to_features_computes_degree_hours() -> None:
    feature_names = BASE_FEATURE_NAMES
    row = {"indoor_temp": 20.0, "outdoor_temp": 5.0, "power_kw": 1.2}

    features = _row_to_features(row, feature_names)

    assert features == [1.0, 15.0, 0.0]  # bias, heating, cooling


def test_row_to_features_returns_none_if_selected_feature_missing() -> None:
    feature_names = BASE_FEATURE_NAMES + ["shortwave_radiation"]
    row = {"indoor_temp": 20.0, "outdoor_temp": 5.0, "power_kw": 1.2}  # no radiation

    assert _row_to_features(row, feature_names) is None


async def test_async_train_weights_reads_from_history_store(hass) -> None:
    entry_id = "test_entry"
    store = HistoryStore(hass, entry_id)

    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    rows = {}
    for i in range(40):
        hour = now - timedelta(hours=i)
        outdoor = -5.0 + (i % 10)
        indoor = 20.0
        power = max(0.0, 0.2 * max(0.0, indoor - outdoor)) + 0.3
        rows[hour.isoformat()] = {
            "power_kw": power,
            "indoor_temp": indoor,
            "outdoor_temp": outdoor,
        }
    await store.async_merge(rows)

    result = await async_train_weights(hass, entry_id, config={}, training_days=30)

    assert result.n_samples == 40
    assert set(result.feature_names) == set(BASE_FEATURE_NAMES)
    assert result.r2 > 0.9  # near-perfect linear relationship by construction


async def test_async_train_weights_raises_on_too_few_samples(hass) -> None:
    entry_id = "sparse_entry"
    store = HistoryStore(hass, entry_id)
    await store.async_merge(
        {"2026-01-01T00:00:00+00:00": {"power_kw": 1.0, "indoor_temp": 20.0, "outdoor_temp": 5.0}}
    )

    with pytest.raises(ValueError, match="Too few usable training data points"):
        await async_train_weights(hass, entry_id, config={}, training_days=30)
