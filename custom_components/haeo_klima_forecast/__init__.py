"""HAEO Klima Forecast integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_CONFIG_ENTRY_ID, DOMAIN, PLATFORMS, SERVICE_RECALCULATE_WEIGHTS
from .coordinator import HaeoForecastCoordinator

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = HaeoForecastCoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # async_refresh() (instead of async_config_entry_first_refresh()) is allowed to
    # fail, e.g. because no weights exist yet - this does NOT abort setup. The user
    # then has to run the recalculate_weights service first.
    await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _handle_recalculate_weights(call: ServiceCall) -> None:
        target_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if target_entry_id:
            if target_entry_id not in hass.data.get(DOMAIN, {}):
                raise HomeAssistantError(
                    f"HAEO Klima Forecast: config entry ID '{target_entry_id}' is not "
                    "(or no longer) known. The integration was likely set up again "
                    "since then and received a new entry ID - please reselect the "
                    "climate system in the service call, or leave the field empty "
                    "to recalculate all systems."
                )
            targets = [hass.data[DOMAIN][target_entry_id]]
        else:
            targets = list(hass.data.get(DOMAIN, {}).values())
        for target in targets:
            await target.async_recalculate_weights()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALCULATE_WEIGHTS,
        _handle_recalculate_weights,
        schema=SERVICE_SCHEMA,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_RECALCULATE_WEIGHTS)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
