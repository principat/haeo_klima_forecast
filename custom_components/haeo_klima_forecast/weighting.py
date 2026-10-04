"""Determination of the weights (regression coefficients) from the HistoryStore.

Feature model ("energy signature" / degree-hours regression, see
SPECIFICATION.md section 1.2/1.3):

  bias                  - constant term (baseline/standby load)
  heating_degree_hours  - max(0, indoor_temp - outdoor_temp)
  cooling_degree_hours  - max(0, outdoor_temp - indoor_temp)
  hour_sin/cos(2)       - local time of day (daily schedule of the unit)
  outdoor_above_<k>     - max(0, outdoor_temp - k): piecewise-linear terms,
                          because the unit modulates within a band
                          [only for knots the training data covers]
  wind_speed            - wind speed (m/s)                     [optional]
  humidity              - relative humidity (%)                [optional]
  wind_direction_sin/    - circular encoding of wind direction  [optional,
  wind_direction_cos      both columns together]                speculative]

The optional features are only included if enough rows in the HistoryStore
actually carry them - a feature that is barely populated would make the
regression unstable rather than more accurate. Target variable y: mean
power in kW per hour, from `power_kw` in the HistoryStore.

Night setback and duty throttling are deliberately not modeled as separate
features: since the HistoryStore's `indoor_temp` is the *actual* measured
temperature (already shaped by both mechanisms), their effect is already
contained in heating_/cooling_degree_hours (see SPECIFICATION.md 1.2).

The regression itself runs as a lightly ridge-regularized least-squares
estimate via numpy - unchanged from earlier iterations of this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .forecast import OUTDOOR_KNOTS, TIME_OF_DAY_FEATURES, feature_values, knot_feature_name
from .history_store import HistoryStore

_LOGGER = logging.getLogger(__name__)

BASE_FEATURE_NAMES = ["bias", "heating_degree_hours", "cooling_degree_hours", *TIME_OF_DAY_FEATURES]

# HistoryStore field name -> feature name, for the simple 1:1 optional features.
OPTIONAL_FIELD_FEATURES = {
    "wind_speed": "wind_speed",
    "humidity": "humidity",
}

# A feature is only included if at least this fraction of the candidate rows
# actually carries a value for it.
MIN_FEATURE_COVERAGE = 0.5

# Missing indoor temperatures are only estimated if at least this many hours
# have a real measurement to base the estimate on.
MIN_MEASURED_INDOOR_ROWS = 24

# An outdoor-temperature knot is only used if at least this many training
# rows lie clearly above it (otherwise the term is all zeros or fitted to noise).
MIN_ROWS_ABOVE_KNOT = 24

RIDGE_ALPHA = 1e-2  # small regularization against multicollinearity (the knot terms overlap)


@dataclass
class TrainingResult:
    coefficients: dict[str, float]
    r2: float
    n_samples: int
    trained_at: str
    feature_names: list[str] = field(default_factory=list)
    max_observed_kw: float | None = None
    # Mean absolute error of the daily energy relative to the mean daily
    # energy (in-sample, full days only) - what the planning actually needs.
    daily_energy_error_pct: float | None = None


class WeightStore:
    """Persists the most recently computed weights per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_weights_{entry_id}")

    async def async_load(self) -> dict | None:
        return await self._store.async_load()

    async def async_save(self, result: TrainingResult) -> None:
        await self._store.async_save(
            {
                "coefficients": result.coefficients,
                "r2": result.r2,
                "n_samples": result.n_samples,
                "trained_at": result.trained_at,
                "feature_names": result.feature_names,
                "max_observed_kw": result.max_observed_kw,
                "daily_energy_error_pct": result.daily_energy_error_pct,
            }
        )


def _fill_missing_indoor_temp(rows: list[dict]) -> list[dict]:
    """Estimates `indoor_temp` for hours that have power and outdoor data but no indoor value.

    The indoor temperature of long-ago hours is often unrecoverable (raw state
    history is purged after a few days). The mean of the measured values is a
    reasonable stand-in for a heated building and keeps those hours usable.
    The estimate exists only in this training run, never in the HistoryStore,
    so these hours drop out on their own once older than `training_days`.
    """
    measured = [r["indoor_temp"] for r in rows if r.get("indoor_temp") is not None]
    if len(measured) < MIN_MEASURED_INDOOR_ROWS:
        return rows
    mean_indoor = sum(measured) / len(measured)
    return [
        {**r, "indoor_temp": mean_indoor} if r.get("indoor_temp") is None and r.get("outdoor_temp") is not None else r
        for r in rows
    ]


def _select_optional_features(rows: list[dict]) -> list[str]:
    """Decides which optional features have enough coverage to be trained."""
    n = len(rows)
    if n == 0:
        return []
    selected: list[str] = []
    for field_name, feature_name in OPTIONAL_FIELD_FEATURES.items():
        coverage = sum(1 for r in rows if r.get(field_name) is not None) / n
        if coverage >= MIN_FEATURE_COVERAGE:
            selected.append(feature_name)
    wind_dir_coverage = sum(1 for r in rows if r.get("wind_direction") is not None) / n
    if wind_dir_coverage >= MIN_FEATURE_COVERAGE:
        selected.extend(["wind_direction_sin", "wind_direction_cos"])
    return selected


def _select_knots(rows: list[dict]) -> list[str]:
    """Outdoor-temperature knots the training data actually covers (see MIN_ROWS_ABOVE_KNOT)."""
    outdoor = [r["outdoor_temp"] for r in rows if r.get("outdoor_temp") is not None]
    if not outdoor:
        return []
    return [
        knot_feature_name(knot)
        for knot in OUTDOOR_KNOTS
        if min(outdoor) < knot and sum(1 for o in outdoor if o > knot + 1) >= MIN_ROWS_ABOVE_KNOT
    ]


def _row_to_features(row: dict, feature_names: list[str]) -> list[float] | None:
    """Builds one regression row, or None if a selected feature is missing here."""
    indoor = row.get("indoor_temp")
    outdoor = row.get("outdoor_temp")
    hour = row.get("hour")
    if indoor is None or outdoor is None or hour is None:
        return None

    values = feature_values(
        outdoor, indoor, hour, row.get("wind_speed"), row.get("humidity"), row.get("wind_direction")
    )
    if any(name not in values for name in feature_names):
        return None
    return [values[name] for name in feature_names]


def _fit(X: np.ndarray, y: np.ndarray, dates: list) -> tuple[np.ndarray, float, float | None]:
    """Ridge least squares; returns coefficients, R² and the daily-energy error (in %).

    Pure numpy and CPU-bound, so the caller runs it in the executor.
    """
    n_features = X.shape[1]
    coeffs = np.linalg.solve(X.T @ X + RIDGE_ALPHA * np.eye(n_features), X.T @ y)

    y_pred = X @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot

    per_day: dict = {}
    for day, actual, predicted in zip(dates, y, y_pred):
        entry = per_day.setdefault(day, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += actual
        entry[2] += predicted
    full_days = [(a, p) for n, a, p in per_day.values() if n == 24]
    daily_error = None
    if full_days:
        mean_daily = sum(a for a, _ in full_days) / len(full_days)
        if mean_daily > 0:
            daily_error = 100.0 * sum(abs(a - p) for a, p in full_days) / len(full_days) / mean_daily
    return coeffs, r2, daily_error


async def async_train_weights(hass: HomeAssistant, entry_id: str, config: dict, training_days: int) -> TrainingResult:
    """Trains the regression over the configured period, reading only from the HistoryStore."""
    store = HistoryStore(hass, entry_id)
    start = dt_util.utcnow() - timedelta(days=training_days)
    all_rows = await store.async_rows_since(start)

    power_rows = []
    for key, row in all_rows.items():
        if row.get("power_kw") is None:
            continue
        # Hour of day in local time: the unit's schedule follows the wall clock, also across DST changes.
        local = dt_util.as_local(dt_util.parse_datetime(key))
        power_rows.append({**row, "hour": local.hour, "date": local.date()})

    candidate_rows = _fill_missing_indoor_temp(power_rows)
    feature_names = BASE_FEATURE_NAMES + _select_knots(candidate_rows) + _select_optional_features(candidate_rows)

    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    dates: list = []
    for row in candidate_rows:
        features = _row_to_features(row, feature_names)
        if features is None:
            continue
        rows_x.append(features)
        rows_y.append(row["power_kw"])
        dates.append(row["date"])

    n_samples = len(rows_y)
    if n_samples < len(feature_names) + 5:
        raise ValueError(
            f"Too few usable training data points ({n_samples}). "
            "Please choose a longer training period or try again later, "
            "once more history is available."
        )

    X = np.array(rows_x)
    y = np.array(rows_y)
    coeffs, r2, daily_error = await hass.async_add_executor_job(_fit, X, y, dates)

    result = TrainingResult(
        coefficients=dict(zip(feature_names, coeffs.tolist())),
        r2=r2,
        n_samples=n_samples,
        trained_at=dt_util.utcnow().isoformat(),
        feature_names=feature_names,
        max_observed_kw=float(np.max(y)),
        daily_energy_error_pct=daily_error,
    )
    _LOGGER.info(
        "HAEO Klima Forecast: weights recalculated (n=%s, R²=%.3f, daily energy error=%s%%): %s",
        n_samples,
        r2,
        f"{daily_error:.0f}" if daily_error is not None else "n/a",
        result.coefficients,
    )
    return result
