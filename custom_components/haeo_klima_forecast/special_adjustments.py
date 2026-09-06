"""Modeling of the two special adjustments as setpoint offsets.

Neither mechanism is "executed" here (that still happens in your existing
automation/logic) - these functions are needed for two purposes:

1. During training (weighting.py): read historical actual offsets so the
   regression correctly attributes the real effect on power.
2. During the forecast (forecast.py): estimate the EXPECTED offset for a
   future hour so the forecast stays realistic.

Night setback is time-based and therefore well predictable for the future.
Duty throttling is load-based/reactive and can NOT be reliably predicted for
the future - it is defensively projected as 0 (no offset) here, unless the
user explicitly configures an assumed average value.
"""
from __future__ import annotations

from datetime import datetime, time

from homeassistant.util import dt as dt_util


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_within_night_window(ts: datetime, start: str, end: str) -> bool:
    """Checks whether a point in time falls within the night setback window.

    `start`/`end` are meant as local time (as entered in the configuration,
    e.g. "22:00"). `ts` typically arrives as a UTC timestamp from the weather
    forecast and is therefore first converted to the HA-configured local
    timezone before the time is compared. Also supports windows spanning
    midnight (e.g. 22:00 - 06:00).
    """
    local_ts = dt_util.as_local(ts) if ts.tzinfo is not None else ts
    t = local_ts.time()
    start_t = _parse_hhmm(start)
    end_t = _parse_hhmm(end)
    if start_t <= end_t:
        return start_t <= t < end_t
    # window spans midnight
    return t >= start_t or t < end_t


def projected_night_setback_offset(
    ts: datetime,
    start: str,
    end: str,
    configured_offset: float,
    hvac_mode: str,
) -> float:
    """Expected night setback offset (in °C) for a forecast point in time.

    The return value already has the correct sign to apply to the setpoint:
    heating -> setback (negative), cooling -> boost (positive), both in the
    sense of "avoid additional load".
    """
    if not is_within_night_window(ts, start, end):
        return 0.0
    magnitude = abs(configured_offset)
    if hvac_mode == "cool":
        return magnitude
    return -magnitude


def projected_duty_throttle_offset(assumed_offset: float = 0.0) -> float:
    """Expected duty throttle offset for the forecast.

    Since this mechanism is load-dependent and reactive, it cannot be
    reliably predicted for future hours. By default 0.0 (no offset) is
    therefore assumed. Optionally, a configured empirical value (e.g. an
    average offset from history) can be passed in to make the forecast more
    conservative.
    """
    return assumed_offset


def step_toward_target(
    current_offset: float,
    target_offset: float,
    step: float,
) -> float:
    """One step of gradually retracting/approaching an offset.

    Useful if duty throttling should be computed within this integration
    (instead of in an external automation). `step` is always positive; the
    function ensures that `target_offset` is not overshot.
    """
    if current_offset < target_offset:
        return min(current_offset + step, target_offset)
    if current_offset > target_offset:
        return max(current_offset - step, target_offset)
    return current_offset
