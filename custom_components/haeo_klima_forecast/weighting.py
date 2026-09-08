"""Determination of the weights (regression coefficients) from historical data.

Feature vector per hour (in this order, see FEATURE_NAMES):
Heating and cooling degree hours are currently separate features; FEATURE_NAMES is authoritative.
  0. bias                 - constant (base load/standby)
  1. degree_hours         - sum over all active indoor units of
                             max(0, setpoint - outdoor temp)  [heating]  resp.
                             max(0, outdoor temp - setpoint)  [cooling]
                             -> physically motivated main factor for the load
  2. shortwave_radiation  - global radiation (W/m²)
  3. wind_speed           - wind speed (m/s)
  4. active_units         - number of active (not "off") indoor units
  5. night_setback_offset - historically effective night setback offset (°C)
  6. duty_throttle_offset - historically effective duty throttle offset (°C)

Target variable y: mean power in kW per hour (from the power sensor).

The regression runs as a simple (lightly ridge-regularized) least-squares
estimate via numpy - deliberately kept simple so that `forecast.py` only
needs to combine the weights with the forecast data afterwards (see forecast.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TypeVar

import numpy as np

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DUTY_THROTTLE_OFFSET_ENTITY,
    CONF_POWER_SENSOR,
    CONF_INDOOR_UNITS,
    CONF_NIGHT_SETBACK_OFFSET_ENTITY,
    CONF_TRAINING_DAYS,
    DEFAULT_TRAINING_DAYS,
    DOMAIN,
    IU_CLIMATE_ENTITY,
    STORAGE_VERSION,
)
from .mirror import (
    SUFFIX_ACTIVE,
    SUFFIX_CURRENT_TEMPERATURE,
    SUFFIX_HVAC_ACTION,
    SUFFIX_HVAC_MODE,
    SUFFIX_SETPOINT,
    WEATHER_SUFFIX_OUTDOOR_TEMPERATURE,
    WEATHER_SUFFIX_SHORTWAVE_RADIATION,
    WEATHER_SUFFIX_WIND_SPEED,
    climate_mirror_unique_id,
    decode_hvac_action,
    decode_hvac_mode,
    resolve_entity_id,
    weather_mirror_unique_id,
)
from .weather.base import WeatherPoint

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")

FEATURE_NAMES = [
    "bias",
    "heating_degree_hours",
    "cooling_degree_hours",
    "shortwave_radiation",
    "wind_speed",
    "active_units",
    "night_setback_offset",
    "duty_throttle_offset",
]

RIDGE_ALPHA = 1e-3  # small regularization against multicollinearity


@dataclass
class TrainingResult:
    coefficients: dict[str, float]
    r2: float
    n_samples: int
    trained_at: str
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))


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


def _resample_last_value(
    samples: list[tuple[datetime, T]], hour_marks: list[datetime]
) -> dict[datetime, T]:
    """Assigns each full hour the most recently known value before it (step function)."""
    samples = sorted(samples, key=lambda s: s[0])
    result: dict[datetime, T] = {}
    idx = 0
    last_value: T | None = None
    for mark in hour_marks:
        while idx < len(samples) and samples[idx][0] <= mark:
            last_value = samples[idx][1]
            idx += 1
        if last_value is not None:
            result[mark] = last_value
    return result


async def _fetch_statistics_mean_per_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Reads the hourly mean of an entity's long-term statistics.

    Note: `statistics_during_period` is a synchronous recorder API (not
    `async def`) and must therefore always run via the recorder executor -
    a direct `await` would execute the DB query blockingly in the event loop
    and be caught as an error by HA's blocking-call detector.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    def _get():
        return statistics_during_period(
            hass,
            start,
            end,
            {entity_id},
            "hour",
            None,
            {"mean"},
        )

    stats = await get_instance(hass).async_add_executor_job(_get)
    rows = stats.get(entity_id, [])
    per_hour: dict[datetime, float] = {}
    for row in rows:
        ts = row["start"] if isinstance(row["start"], datetime) else dt_util.utc_from_timestamp(row["start"])
        mean = row.get("mean")
        if mean is None:
            continue
        per_hour[ts] = mean
    return per_hour


async def _fetch_power_per_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Reads the power (kW) per hour from the HA long-term statistics."""
    return await _fetch_statistics_mean_per_hour(hass, entity_id, start, end)


async def _fetch_mirror_mean_per_hour(
    hass: HomeAssistant, unique_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """Reads the hourly mean of one of our own recorder-mirror sensors (see mirror.py).

    Returns an empty dict for hours the mirror does not (yet) cover - e.g.
    before the integration was set up, or before a mirror sensor's unique_id
    resolves to an entity_id at all. Callers merge this with a fallback
    (raw climate-entity history / the weather provider's archive API).
    """
    entity_id = resolve_entity_id(hass, unique_id)
    if entity_id is None:
        return {}
    return await _fetch_statistics_mean_per_hour(hass, entity_id, start, end)


async def _fetch_state_history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Reads the numeric state history of an entity (e.g. setpoint temperature)."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import state_changes_during_period

    def _get():
        return state_changes_during_period(hass, start, end, entity_id)

    history = await get_instance(hass).async_add_executor_job(_get)
    samples: list[tuple[datetime, float]] = []
    for state in history.get(entity_id, []):
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            # e.g. for climate entities: the setpoint is in the attribute
            value = state.attributes.get("temperature")
            if value is None:
                continue
            try:
                value = float(value)
            except (ValueError, TypeError):
                continue
        samples.append((state.last_changed, value))
    return samples


async def _fetch_climate_active_history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Returns 1.0 when the indoor unit is not 'off', otherwise 0.0 - per state change."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import state_changes_during_period

    def _get():
        return state_changes_during_period(hass, start, end, entity_id)

    history = await get_instance(hass).async_add_executor_job(_get)
    samples: list[tuple[datetime, float]] = []
    for state in history.get(entity_id, []):
        samples.append((state.last_changed, 0.0 if state.state in ("off", "unavailable", "unknown") else 1.0))
    return samples


async def _fetch_climate_mode_history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, tuple[str | None, str | None, float | None]]]:
    """Reads HVAC mode, HVAC action and current temperature of a climate entity."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import state_changes_during_period

    def _get():
        return state_changes_during_period(hass, start, end, entity_id)

    history = await get_instance(hass).async_add_executor_job(_get)
    samples: list[tuple[datetime, tuple[str | None, str | None, float | None]]] = []
    for state in history.get(entity_id, []):
        current_temp = state.attributes.get("current_temperature")
        try:
            current_temp = float(current_temp) if current_temp is not None else None
        except (ValueError, TypeError):
            current_temp = None
        samples.append(
            (
                state.last_changed,
                (
                    state.state,
                    state.attributes.get("hvac_action"),
                    current_temp,
                ),
            )
        )
    return samples


async def _fetch_climate_setpoint_by_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, hour_marks: list[datetime]
) -> dict[datetime, float]:
    """Setpoint temperature per hour: recorder-mirror statistics first, raw state history as fallback."""
    mirror = await _fetch_mirror_mean_per_hour(
        hass, climate_mirror_unique_id(entity_id, SUFFIX_SETPOINT), start, end
    )
    fallback: dict[datetime, float] = {}
    if any(mark not in mirror for mark in hour_marks):
        raw = await _fetch_state_history(hass, entity_id, start, end)
        fallback = _resample_last_value(raw, hour_marks)
    return {**fallback, **mirror}


async def _fetch_climate_active_by_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, hour_marks: list[datetime]
) -> dict[datetime, float]:
    """Active fraction per hour: recorder-mirror statistics first, raw state history as fallback."""
    mirror = await _fetch_mirror_mean_per_hour(
        hass, climate_mirror_unique_id(entity_id, SUFFIX_ACTIVE), start, end
    )
    fallback: dict[datetime, float] = {}
    if any(mark not in mirror for mark in hour_marks):
        raw = await _fetch_climate_active_history(hass, entity_id, start, end)
        fallback = _resample_last_value(raw, hour_marks)
    return {**fallback, **mirror}


async def _fetch_climate_mode_by_hour(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, hour_marks: list[datetime]
) -> dict[datetime, tuple[str | None, str | None, float | None]]:
    """HVAC mode/action/current-temp per hour: recorder-mirror statistics first, raw history as fallback."""
    mode_mirror = await _fetch_mirror_mean_per_hour(
        hass, climate_mirror_unique_id(entity_id, SUFFIX_HVAC_MODE), start, end
    )
    action_mirror = await _fetch_mirror_mean_per_hour(
        hass, climate_mirror_unique_id(entity_id, SUFFIX_HVAC_ACTION), start, end
    )
    temp_mirror = await _fetch_mirror_mean_per_hour(
        hass, climate_mirror_unique_id(entity_id, SUFFIX_CURRENT_TEMPERATURE), start, end
    )

    fallback: dict[datetime, tuple[str | None, str | None, float | None]] = {}
    if any(mark not in mode_mirror for mark in hour_marks):
        raw = await _fetch_climate_mode_history(hass, entity_id, start, end)
        fallback = _resample_last_value(raw, hour_marks)

    result: dict[datetime, tuple[str | None, str | None, float | None]] = dict(fallback)
    for mark, mode_value in mode_mirror.items():
        result[mark] = (
            decode_hvac_mode(mode_value),
            decode_hvac_action(action_mirror.get(mark)),
            temp_mirror.get(mark),
        )
    return result


async def _fetch_weather_by_hour(
    hass: HomeAssistant, entry_id: str, provider, start: datetime, end: datetime, hour_marks: list[datetime]
) -> dict[datetime, WeatherPoint]:
    """Historical weather per hour: our own recorder-mirror statistics first (see mirror.py).

    Only the hours the mirrors do not cover yet (typically the period before
    this integration - and thus the mirror sensors - existed) fall back to
    the weather provider's own historical archive API, so training does not
    depend solely on that external API once enough own history has built up.
    """
    temp_mirror = await _fetch_mirror_mean_per_hour(
        hass, weather_mirror_unique_id(entry_id, WEATHER_SUFFIX_OUTDOOR_TEMPERATURE), start, end
    )
    radiation_mirror = await _fetch_mirror_mean_per_hour(
        hass, weather_mirror_unique_id(entry_id, WEATHER_SUFFIX_SHORTWAVE_RADIATION), start, end
    )
    wind_mirror = await _fetch_mirror_mean_per_hour(
        hass, weather_mirror_unique_id(entry_id, WEATHER_SUFFIX_WIND_SPEED), start, end
    )

    missing_marks = [mark for mark in hour_marks if mark not in temp_mirror]
    api_by_hour: dict[datetime, WeatherPoint] = {}
    if missing_marks:
        gap_start, gap_end = min(missing_marks), max(missing_marks) + timedelta(hours=1)
        api_points = await provider.async_get_historical(gap_start, gap_end)
        api_by_hour = {
            p.timestamp.replace(minute=0, second=0, microsecond=0): p for p in api_points
        }

    result: dict[datetime, WeatherPoint] = {}
    for mark in hour_marks:
        if mark in temp_mirror:
            result[mark] = WeatherPoint(
                timestamp=mark,
                temperature_c=temp_mirror[mark],
                shortwave_radiation=radiation_mirror.get(mark),
                wind_speed_ms=wind_mirror.get(mark),
            )
        elif mark in api_by_hour:
            result[mark] = api_by_hour[mark]
    return result


def _hourly_marks(start: datetime, end: datetime) -> list[datetime]:
    marks = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur <= end:
        marks.append(cur)
        cur += timedelta(hours=1)
    return marks


async def async_train_weights(
    hass: HomeAssistant, entry_id: str, config: dict, provider
) -> TrainingResult:
    """Runs the regression over the configured historical time period."""
    training_days = config.get(CONF_TRAINING_DAYS, DEFAULT_TRAINING_DAYS)
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=training_days)
    hour_marks = _hourly_marks(start, end)

    weather_by_hour = await _fetch_weather_by_hour(hass, entry_id, provider, start, end, hour_marks)

    power_by_hour = await _fetch_power_per_hour(
        hass, config[CONF_POWER_SENSOR], start, end
    )

    indoor_units = config.get(CONF_INDOOR_UNITS, [])
    setpoint_series: list[dict[datetime, float]] = []
    active_series: list[dict[datetime, float]] = []
    mode_series: list[dict[datetime, tuple[str | None, str | None, float | None]]] = []
    for unit in indoor_units:
        entity_id = unit.get(IU_CLIMATE_ENTITY)
        if not entity_id:
            continue
        setpoint_series.append(
            await _fetch_climate_setpoint_by_hour(hass, entity_id, start, end, hour_marks)
        )
        active_series.append(
            await _fetch_climate_active_by_hour(hass, entity_id, start, end, hour_marks)
        )
        mode_series.append(
            await _fetch_climate_mode_by_hour(hass, entity_id, start, end, hour_marks)
        )

    night_offset_by_hour: dict[datetime, float] = {}
    if config.get(CONF_NIGHT_SETBACK_OFFSET_ENTITY):
        raw = await _fetch_state_history(
            hass, config[CONF_NIGHT_SETBACK_OFFSET_ENTITY], start, end
        )
        night_offset_by_hour = _resample_last_value(raw, hour_marks)

    duty_offset_by_hour: dict[datetime, float] = {}
    if config.get(CONF_DUTY_THROTTLE_OFFSET_ENTITY):
        raw = await _fetch_state_history(
            hass, config[CONF_DUTY_THROTTLE_OFFSET_ENTITY], start, end
        )
        duty_offset_by_hour = _resample_last_value(raw, hour_marks)

    rows_x: list[list[float]] = []
    rows_y: list[float] = []

    for mark in hour_marks:
        weather = weather_by_hour.get(mark)
        power = power_by_hour.get(mark)
        if weather is None or power is None or weather.temperature_c is None:
            continue

        night_offset = night_offset_by_hour.get(mark, 0.0)
        duty_offset = duty_offset_by_hour.get(mark, 0.0)

        heating_degree_hours = 0.0
        cooling_degree_hours = 0.0
        active_units = 0.0
        for sp_series, act_series, hvac_series in zip(setpoint_series, active_series, mode_series):
            setpoint = sp_series.get(mark)
            active = act_series.get(mark, 0.0)
            if setpoint is None or active < 0.5:
                continue
            active_units += 1
            hvac_mode, hvac_action, current_temp = hvac_series.get(mark, (None, None, None))
            mode = _resolve_hvac_mode(hvac_mode, hvac_action, current_temp, setpoint, weather.temperature_c)
            if mode == "cool":
                cooling_degree_hours += max(0.0, weather.temperature_c - setpoint)
            else:
                heating_degree_hours += max(0.0, setpoint - weather.temperature_c)

        row = [
            1.0,
            heating_degree_hours,
            cooling_degree_hours,
            weather.shortwave_radiation or 0.0,
            weather.wind_speed_ms or 0.0,
            active_units,
            night_offset,
            duty_offset,
        ]
        rows_x.append(row)
        rows_y.append(power)

    n_samples = len(rows_y)
    if n_samples < len(FEATURE_NAMES) + 5:
        raise ValueError(
            f"Too few usable training data points ({n_samples}). "
            "Please choose a longer training period or try again later, "
            "once more history is available."
        )

    X = np.array(rows_x)
    y = np.array(rows_y)

    # Ridge-regularized least-squares solution: (XᵀX + αI)⁻¹Xᵀy
    n_features = X.shape[1]
    XtX = X.T @ X + RIDGE_ALPHA * np.eye(n_features)
    Xty = X.T @ y
    coeffs = np.linalg.solve(XtX, Xty)

    y_pred = X @ coeffs
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-9
    r2 = 1.0 - ss_res / ss_tot

    result = TrainingResult(
        coefficients=dict(zip(FEATURE_NAMES, coeffs.tolist())),
        r2=r2,
        n_samples=n_samples,
        trained_at=dt_util.utcnow().isoformat(),
    )
    _LOGGER.info(
        "HAEO Klima Forecast: weights recalculated (n=%s, R²=%.3f): %s",
        n_samples,
        r2,
        result.coefficients,
    )
    return result


def _hvac_mode_guess(setpoint: float, outdoor_temp: float) -> str:
    """Very simple heuristic in case no explicit hvac_mode is available.

    Only used as a fallback for the regression (sign of the degree-hours
    term). The actual forecast uses the real hvac_mode of the climate
    entity, see coordinator.py.
    """
    return "cool" if outdoor_temp > setpoint else "heat"


def _resolve_hvac_mode(
    hvac_mode: str | None,
    hvac_action: str | None,
    current_temp: float | None,
    setpoint: float,
    outdoor_temp: float,
) -> str:
    """Resolve a historical climate state to heating or cooling load."""
    if hvac_action == "cooling":
        return "cool"
    if hvac_action == "heating":
        return "heat"
    if hvac_mode == "cool":
        return "cool"
    if hvac_mode == "heat":
        return "heat"
    if current_temp is not None:
        return "cool" if current_temp > setpoint else "heat"
    return "cool" if outdoor_temp > setpoint else "heat"
