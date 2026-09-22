"""Switch entity: weekly automatic recalculation of the weights.

Instead of creating a real HA automation (which would mean writing into the
user's automation config and leaving it behind when the integration is
removed), the switch schedules an internal time trigger while it is on.
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    AUTO_RECALCULATE_HOUR,
    AUTO_RECALCULATE_MINUTE,
    AUTO_RECALCULATE_WEEKDAY,
    DOMAIN,
)
from .coordinator import HaeoForecastCoordinator
from .entity import HaeoBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HaeoForecastCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HaeoAutoRecalculateSwitch(coordinator, entry)])


class HaeoAutoRecalculateSwitch(HaeoBaseEntity, SwitchEntity, RestoreEntity):
    """When on, the weights are recalculated every Sunday at 03:30 (local time)."""

    _attr_icon = "mdi:calendar-sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_auto_recalculate_weights"
        self._attr_name = f"{self._system_name} Weekly Weight Recalculation"
        self._attr_is_on = False
        self._unsub_timer: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        # Independent of whether the last forecast refresh succeeded.
        return True

    @property
    def extra_state_attributes(self):
        return {
            "weekday": AUTO_RECALCULATE_WEEKDAY,
            "time": f"{AUTO_RECALCULATE_HOUR:02d}:{AUTO_RECALCULATE_MINUTE:02d}",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == STATE_ON:
            self._attr_is_on = True
            self._start_timer()

    async def async_will_remove_from_hass(self) -> None:
        self._stop_timer()
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self._start_timer()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self._stop_timer()
        self.async_write_ha_state()

    def _start_timer(self) -> None:
        if self._unsub_timer is not None:
            return
        self._unsub_timer = async_track_time_change(
            self.hass,
            self._async_on_time,
            hour=AUTO_RECALCULATE_HOUR,
            minute=AUTO_RECALCULATE_MINUTE,
            second=0,
        )

    def _stop_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def _async_on_time(self, now: datetime) -> None:
        if now.weekday() != AUTO_RECALCULATE_WEEKDAY:
            return
        try:
            await self.coordinator.async_recalculate_weights()
        except Exception:  # noqa: BLE001 - a failed run must not break the schedule
            _LOGGER.exception(
                "HAEO Klima Forecast: scheduled weight recalculation for '%s' failed",
                self._system_name,
            )
