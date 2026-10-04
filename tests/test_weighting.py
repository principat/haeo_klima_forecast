"""Tests for the training/regression logic in weighting.py."""
from __future__ import annotations

import numpy as np
import pytest

from custom_components.haeo_klima_forecast.history_store import HistoryStore
from custom_components.haeo_klima_forecast.weighting import (
    BASE_FEATURE_NAMES,
    _fill_missing_indoor_temp,
    _fit,
    _row_to_features,
    _select_knots,
    _select_optional_features,
    async_train_weights,
)


def test_select_optional_features_requires_minimum_coverage() -> None:
    rows = [{"humidity": 50.0}, {"humidity": 55.0}, {}, {}]  # 50% coverage -> included
    assert _select_optional_features(rows) == ["humidity"]

    sparse_rows = [{"wind_speed": 3.0}, {}, {}, {}]  # 25% coverage -> excluded
    assert _select_optional_features(sparse_rows) == []


def test_select_optional_features_wind_direction_adds_two_columns() -> None:
    rows = [{"wind_direction": 90.0}, {"wind_direction": 180.0}]
    assert _select_optional_features(rows) == ["wind_direction_sin", "wind_direction_cos"]


def test_row_to_features_computes_degree_hours() -> None:
    feature_names = BASE_FEATURE_NAMES
    row = {"indoor_temp": 20.0, "outdoor_temp": 5.0, "power_kw": 1.2, "hour": 12}

    features = _row_to_features(row, feature_names)

    # bias, heating, cooling, hour_sin, hour_cos, hour_sin2, hour_cos2 (noon)
    assert features == pytest.approx([1.0, 15.0, 0.0, 0.0, -1.0, 0.0, 1.0], abs=1e-9)


def test_row_to_features_returns_none_if_selected_feature_missing() -> None:
    feature_names = BASE_FEATURE_NAMES + ["humidity"]
    row = {"indoor_temp": 20.0, "outdoor_temp": 5.0, "power_kw": 1.2, "hour": 12}  # no humidity

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


def test_fill_missing_indoor_temp_uses_mean_only_where_other_data_exists() -> None:
    measured = [{"power_kw": 1.0, "indoor_temp": 20.0 + (i % 2), "outdoor_temp": 5.0} for i in range(24)]
    no_indoor = {"power_kw": 1.0, "outdoor_temp": 5.0}
    no_outdoor = {"power_kw": 1.0}

    filled = _fill_missing_indoor_temp(measured + [no_indoor, no_outdoor])

    assert filled[24]["indoor_temp"] == pytest.approx(20.5)
    assert "indoor_temp" not in filled[25]  # no weather -> not usable, stays out
    assert "indoor_temp" not in no_indoor  # input is not mutated


def test_fill_missing_indoor_temp_needs_enough_measurements() -> None:
    rows = [{"power_kw": 1.0, "indoor_temp": 20.0, "outdoor_temp": 5.0}] * 3 + [
        {"power_kw": 1.0, "outdoor_temp": 5.0}
    ]

    assert "indoor_temp" not in _fill_missing_indoor_temp(rows)[3]


async def test_async_train_weights_raises_on_too_few_samples(hass) -> None:
    entry_id = "sparse_entry"
    store = HistoryStore(hass, entry_id)
    await store.async_merge(
        {"2026-01-01T00:00:00+00:00": {"power_kw": 1.0, "indoor_temp": 20.0, "outdoor_temp": 5.0}}
    )

    with pytest.raises(ValueError, match="Too few usable training data points"):
        await async_train_weights(hass, entry_id, config={}, training_days=30)


def test_select_knots_only_uses_knots_the_data_covers() -> None:
    rows = [{"outdoor_temp": -5.0 + 0.5 * i} for i in range(80)]  # -5 .. 34.5 °C

    knots = _select_knots(rows)

    assert "outdoor_above_0" in knots
    assert "outdoor_above_30" not in knots  # only 7 rows above 31 °C, below the minimum
    assert _select_knots([{"outdoor_temp": 8.0}] * 100) == []  # no spread, no knot


def test_fit_reports_daily_energy_error() -> None:
    n = 48  # two full days
    X = np.c_[np.ones(n), np.tile(np.arange(24), 2)].astype(float)
    y = 1.0 + 0.5 * X[:, 1]
    dates = ["day1"] * 24 + ["day2"] * 24

    coeffs, r2, daily_error = _fit(X, y, dates)

    assert r2 > 0.999
    assert daily_error is not None and daily_error < 1.0


def test_fit_daily_energy_error_none_without_full_days() -> None:
    X = np.c_[np.ones(10), np.arange(10)].astype(float)

    _, _, daily_error = _fit(X, X[:, 1], ["day1"] * 10)

    assert daily_error is None
