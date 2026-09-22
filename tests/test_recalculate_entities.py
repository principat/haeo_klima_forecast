"""Integration tests: recalculate-weights button and weekly auto-recalculation switch."""
from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.haeo_klima_forecast.const import CONF_NAME, DOMAIN

COORDINATOR = "custom_components.haeo_klima_forecast.coordinator.HaeoForecastCoordinator"


@pytest.fixture
def recalc_calls(monkeypatch):
    calls: list[str] = []

    async def _fake_recalculate(self) -> None:
        calls.append(self.entry.entry_id)

    monkeypatch.setattr(f"{COORDINATOR}.async_recalculate_weights", _fake_recalculate)
    return calls


@pytest.fixture
def config_entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NAME: "Test System"})
    entry.add_to_hass(hass)
    return entry


def _entity_id(hass, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def _setup(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_button_triggers_recalculation(hass, config_entry, recalc_calls) -> None:
    await _setup(hass, config_entry)
    button = _entity_id(hass, "button", f"{config_entry.entry_id}_recalculate_weights")

    # Pressable although no weights exist yet (coordinator refresh failed).
    assert hass.states.get(button).state != "unavailable"

    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert recalc_calls == [config_entry.entry_id]


async def test_button_surfaces_training_errors(hass, config_entry, monkeypatch) -> None:
    async def _fail(self) -> None:
        raise ValueError("not enough data")

    monkeypatch.setattr(f"{COORDINATOR}.async_recalculate_weights", _fail)
    await _setup(hass, config_entry)
    button = _entity_id(hass, "button", f"{config_entry.entry_id}_recalculate_weights")

    with pytest.raises(HomeAssistantError, match="not enough data"):
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)


async def test_switch_recalculates_weekly_only_while_on(
    hass, config_entry, recalc_calls, freezer
) -> None:
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 9, 19, 12, 0, tzinfo=tz))  # Saturday
    await _setup(hass, config_entry)
    switch = _entity_id(hass, "switch", f"{config_entry.entry_id}_auto_recalculate_weights")
    assert hass.states.get(switch).state == "off"

    async def _jump_to(when: datetime) -> None:
        freezer.move_to(when)
        async_fire_time_changed(hass, when)
        await hass.async_block_till_done()

    # Off: Sunday 03:30 passes without a run.
    await _jump_to(datetime(2026, 9, 20, 3, 30, tzinfo=tz))
    assert recalc_calls == []

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"

    # Monday 03:30: not the configured weekday.
    await _jump_to(datetime(2026, 9, 21, 3, 30, tzinfo=tz))
    assert recalc_calls == []

    # Next Sunday 03:30: runs.
    await _jump_to(datetime(2026, 9, 27, 3, 30, tzinfo=tz))
    assert recalc_calls == [config_entry.entry_id]

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await _jump_to(datetime(2026, 10, 4, 3, 30, tzinfo=tz))
    assert recalc_calls == [config_entry.entry_id]


async def test_switch_state_is_restored(hass, config_entry) -> None:
    unique_id = f"{config_entry.entry_id}_auto_recalculate_weights"
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, unique_id, config_entry=config_entry, suggested_object_id="auto_recalc"
    )
    mock_restore_cache(hass, [State("switch.auto_recalc", "on")])

    await _setup(hass, config_entry)
    assert hass.states.get("switch.auto_recalc").state == "on"
