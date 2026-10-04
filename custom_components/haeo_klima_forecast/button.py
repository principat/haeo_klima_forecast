"""Button entities: recalculate the weights of a climate system / export its history on demand."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import HaeoForecastCoordinator
from .entity import HaeoBaseEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: HaeoForecastCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HaeoRecalculateWeightsButton(coordinator, entry), HaeoExportHistoryButton(coordinator, entry)])


class HaeoRecalculateWeightsButton(HaeoBaseEntity, ButtonEntity):
    """Same as the `recalculate_weights` service, limited to this climate system."""

    _attr_icon = "mdi:calculator-variant"

    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_recalculate_weights"
        self._attr_name = f"{self._system_name} Recalculate Weights"

    @property
    def available(self) -> bool:
        # Must stay pressable before the first training, when the coordinator
        # refresh still fails for lack of weights.
        return True

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_recalculate_weights()
        except ValueError as err:
            raise HomeAssistantError(f"HAEO Klima Forecast: {err}") from err


class HaeoExportHistoryButton(HaeoBaseEntity, ButtonEntity):
    """Same as the `export_history` service, limited to this climate system."""

    _attr_icon = "mdi:file-export-outline"

    def __init__(self, coordinator: HaeoForecastCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_export_history"
        self._attr_name = f"{self._system_name} Export History"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_export_history()
