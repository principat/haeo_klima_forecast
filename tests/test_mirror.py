"""Unit tests for the pure helper functions in mirror.py (no hass instance needed)."""
from custom_components.haeo_klima_forecast.mirror import (
    climate_mirror_unique_id,
    decode_hvac_action,
    decode_hvac_mode,
    encode_hvac_action,
    encode_hvac_mode,
    weather_mirror_unique_id,
)


def test_encode_decode_hvac_mode_roundtrip() -> None:
    assert encode_hvac_mode("heat") == 1.0
    assert encode_hvac_mode("cool") == -1.0
    assert encode_hvac_mode("off") == 0.0
    assert encode_hvac_mode(None) == 0.0

    assert decode_hvac_mode(1.0) == "heat"
    assert decode_hvac_mode(-1.0) == "cool"
    assert decode_hvac_mode(0.0) is None
    assert decode_hvac_mode(None) is None


def test_encode_decode_hvac_action_roundtrip() -> None:
    assert encode_hvac_action("heating") == 1.0
    assert encode_hvac_action("cooling") == -1.0
    assert encode_hvac_action("idle") == 0.0

    assert decode_hvac_action(1.0) == "heating"
    assert decode_hvac_action(-1.0) == "cooling"
    assert decode_hvac_action(0.0) is None


def test_decode_tolerates_averaged_statistics_values() -> None:
    """A long-term-statistics mean over an hour with mixed states is fractional, not exactly ±1."""
    assert decode_hvac_mode(0.8) == "heat"
    assert decode_hvac_mode(-0.6) == "cool"
    assert decode_hvac_mode(0.1) is None


def test_unique_ids_are_stable_and_distinct() -> None:
    a = climate_mirror_unique_id("climate.living_room", "setpoint_temperature")
    b = climate_mirror_unique_id("climate.living_room", "active")
    c = climate_mirror_unique_id("climate.bedroom", "setpoint_temperature")

    assert a != b
    assert a != c
    assert a == climate_mirror_unique_id("climate.living_room", "setpoint_temperature")

    assert weather_mirror_unique_id("entry1", "outdoor_temperature") != weather_mirror_unique_id(
        "entry1", "wind_speed"
    )
