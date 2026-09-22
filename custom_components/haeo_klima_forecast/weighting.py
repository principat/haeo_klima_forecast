"""Determination of the weights (regression coefficients) from the HistoryStore.

Feature model ("energy signature" / degree-hours regression, see
SPECIFICATION.md section 1.2/1.3):

  bias                  - constant term (baseline/standby load)
  heating_degree_hours  - max(0, indoor_temp - outdoor_temp)
  cooling_degree_hours  - max(0, outdoor_temp - indoor_temp)
  shortwave_radiation   - global radiation (W/m²)              [optional]
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
from .history_store import HistoryStore

_LOGGER = logging.getLogger(__name__)

BASE_FEATURE_NAMES = ["bias", "heating_degree_hours", "cooling_degree_hours"]

# HistoryStore field name -> feature name, for the simple 1:1 optional features.
OPTIONAL_FIELD_FEATURES = {
    "radiation": "shortwave_radiation",
    "wind_speed": "wind_speed",
    "humidity": "humidity",
}

# A feature is only included if at least this fraction of the candidate rows
# actually carries a value for it.
MIN_FEATURE_COVERAGE = 0.5

RIDGE_ALPHA = 1e-3  # small regularization against multicollinearity


@dataclass
class TrainingResult:
    coefficients: dict[str, float]
    r2: float
    n_samples: int
    trained_at: str
    feature_names: list[str] = field(default_factory=list)


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
            }
        )


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


def _row_to_features(row: dict, feature_names: list[str]) -> list[float] | None:
    """Builds one regression row, or None if a selected feature is missing here."""
    indoor = row.get("indoor_temp")
    outdoor = row.get("outdoor_temp")
    if indoor is None or outdoor is None:
        return None

    values: dict[str, float] = {
        "bias": 1.0,
        "heating_degree_hours": max(0.0, indoor - outdoor),
        "cooling_degree_hours": max(0.0, outdoor - indoor),
    }
    if row.get("radiation") is not None:
        values["shortwave_radiation"] = row["radiation"]
    if row.get("wind_speed") is not None:
        values["wind_speed"] = row["wind_speed"]
    if row.get("humidity") is not None:
        values["humidity"] = row["humidity"]
    if row.get("wind_direction") is not None:
        radians = np.deg2rad(row["wind_direction"])
        values["wind_direction_sin"] = float(np.sin(radians))
        values["wind_direction_cos"] = float(np.cos(radians))

    if any(name not in values for name in feature_names):
        return None
    return [values[name] for name in feature_names]


async def async_train_weights(hass: HomeAssistant, entry_id: str, config: dict, training_days: int) -> TrainingResult:
    """Trains the regression over the configured period, reading only from the HistoryStore."""
    store = HistoryStore(hass, entry_id)
    start = dt_util.utcnow() - timedelta(days=training_days)
    all_rows = await store.async_rows_since(start)

    candidate_rows = [r for r in all_rows.values() if r.get("power_kw") is not None]
    optional_features = _select_optional_features(candidate_rows)
    feature_names = BASE_FEATURE_NAMES + optional_features

    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    for row in candidate_rows:
        features = _row_to_features(row, feature_names)
        if features is None:
            continue
        rows_x.append(features)
        rows_y.append(row["power_kw"])

    n_samples = len(rows_y)
    if n_samples < len(feature_names) + 5:
        raise ValueError(
            f"Too few usable training data points ({n_samples}). "
            "Please choose a longer training period or try again later, "
            "once more history is available."
        )

    X = np.array(rows_x)
    y = np.array(rows_y)

    n_features = X.shape[1]
    XtX = X.T @ X + RIDGE_ALPHA * np.eye(n_features)
    Xty = X.T @ y
    coeffs = np.linalg.solve(XtX, Xty)

    y_pred = X @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot

    result = TrainingResult(
        coefficients=dict(zip(feature_names, coeffs.tolist())),
        r2=r2,
        n_samples=n_samples,
        trained_at=dt_util.utcnow().isoformat(),
        feature_names=feature_names,
    )
    _LOGGER.info(
        "HAEO Klima Forecast: weights recalculated (n=%s, R²=%.3f): %s",
        n_samples,
        r2,
        result.coefficients,
    )
    return result
