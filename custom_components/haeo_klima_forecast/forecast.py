"""Computation of the power forecast.

Deliberately kept as pure, simple functions (no I/O, no HA objects): the
coordinator collects the input data (weather forecast, current setpoints,
night setback time window, weights) and passes it here for calculation.
This keeps the core logic easily testable and independent of the concrete
weather provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .special_adjustments import projected_duty_throttle_offset, projected_night_setback_offset
from .weather.base import WeatherPoint


@dataclass
class IndoorUnitPlan:
    """Planning basis for an indoor unit at the time the forecast is created."""

    name: str
    base_setpoint: float
    hvac_mode: str  # "heat" or "cool"
    is_active: bool = True


@dataclass
class ForecastHour:
    timestamp: datetime
    predicted_kw: float
    degree_hours: float
    heating_degree_hours: float
    cooling_degree_hours: float
    outdoor_temp: float
    night_setback_offset: float
    duty_throttle_offset: float


def compute_hour(
    weather: WeatherPoint,
    units: list[IndoorUnitPlan],
    coefficients: dict[str, float],
    night_setback_cfg: dict | None,
    duty_throttle_assumed_offset: float = 0.0,
) -> ForecastHour:
    """Computes the predicted power for a single hour."""

    night_offset = 0.0
    if night_setback_cfg and night_setback_cfg.get("enabled"):
        # The offset applies equally to all indoor units of the system (see const.py:
        # night setback is currently configured per system, not per indoor unit).
        any_mode = units[0].hvac_mode if units else "heat"
        night_offset = projected_night_setback_offset(
            weather.timestamp,
            night_setback_cfg["start"],
            night_setback_cfg["end"],
            night_setback_cfg["offset"],
            any_mode,
        )

    duty_offset = projected_duty_throttle_offset(duty_throttle_assumed_offset)

    heating_degree_hours = 0.0
    cooling_degree_hours = 0.0
    applied_night_offsets: list[float] = []
    active_units = 0.0
    for unit in units:
        if not unit.is_active:
            continue
        active_units += 1
        if night_setback_cfg and night_setback_cfg.get("enabled"):
            night_offset = projected_night_setback_offset(
                weather.timestamp,
                night_setback_cfg["start"],
                night_setback_cfg["end"],
                night_setback_cfg["offset"],
                unit.hvac_mode,
            )
        else:
            night_offset = 0.0
        applied_night_offsets.append(night_offset)
        effective_setpoint = unit.base_setpoint + night_offset + duty_offset
        if unit.hvac_mode == "cool":
            cooling_degree_hours += max(0.0, weather.temperature_c - effective_setpoint)
        else:
            heating_degree_hours += max(0.0, effective_setpoint - weather.temperature_c)

    degree_hours = heating_degree_hours + cooling_degree_hours
    night_offset = (
        sum(applied_night_offsets) / len(applied_night_offsets)
        if applied_night_offsets
        else 0.0
    )
    legacy_degree_coeff = coefficients.get("degree_hours", 0.0)

    predicted = (
        coefficients.get("bias", 0.0)
        + coefficients.get("heating_degree_hours", legacy_degree_coeff) * heating_degree_hours
        + coefficients.get("cooling_degree_hours", legacy_degree_coeff) * cooling_degree_hours
        + coefficients.get("shortwave_radiation", 0.0) * (weather.shortwave_radiation or 0.0)
        + coefficients.get("wind_speed", 0.0) * (weather.wind_speed_ms or 0.0)
        + coefficients.get("active_units", 0.0) * active_units
        + coefficients.get("night_setback_offset", 0.0) * night_offset
        + coefficients.get("duty_throttle_offset", 0.0) * duty_offset
    )
    predicted = max(0.0, predicted)  # negative forecasts make no physical sense

    return ForecastHour(
        timestamp=weather.timestamp,
        predicted_kw=predicted,
        degree_hours=degree_hours,
        heating_degree_hours=heating_degree_hours,
        cooling_degree_hours=cooling_degree_hours,
        outdoor_temp=weather.temperature_c,
        night_setback_offset=night_offset,
        duty_throttle_offset=duty_offset,
    )


def compute_forecast_series(
    weather_points: list[WeatherPoint],
    units: list[IndoorUnitPlan],
    coefficients: dict[str, float],
    night_setback_cfg: dict | None,
    duty_throttle_assumed_offset: float = 0.0,
) -> list[ForecastHour]:
    """Computes the complete hourly forecast series for the forecast horizon."""
    return [
        compute_hour(wp, units, coefficients, night_setback_cfg, duty_throttle_assumed_offset)
        for wp in weather_points
    ]
