"""HAEO Klima Forecast integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_FORECAST_HOURS,
    CONF_WEATHER_FORECAST_ENTITY,
    DEFAULT_FORECAST_HOURS,
    DOMAIN,
    PLATFORMS,
    SERVICE_EXPORT_HISTORY,
    SERVICE_RECALCULATE_WEIGHTS,
)
from .coordinator import HaeoForecastCoordinator
from .download import HaeoExportDownloadView
from .weather.forecast_template import async_get_forecast_points

_VIEW_REGISTERED = f"{DOMAIN}_download_view_registered"  # views cannot be unregistered

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    config = {**entry.data, **entry.options}
    weather_entity = config.get(CONF_WEATHER_FORECAST_ENTITY)
    if weather_entity is not None:
        # Do not load the integration until the weather forecast is available, otherwise
        # the sensors stay empty and HAEO hangs waiting for data. HA retries with backoff.
        hours = config.get(CONF_FORECAST_HOURS, DEFAULT_FORECAST_HOURS)
        if not await async_get_forecast_points(hass, weather_entity, hours):
            raise ConfigEntryNotReady(
                f"Weather forecast of '{weather_entity}' is not available yet"
            )

    coordinator = HaeoForecastCoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    if not hass.data.get(_VIEW_REGISTERED):
        hass.http.register_view(HaeoExportDownloadView(hass))
        hass.data[_VIEW_REGISTERED] = True

    # async_refresh() (instead of async_config_entry_first_refresh()) is allowed to
    # fail, e.g. because no weights exist yet - this does NOT abort setup. The user
    # then has to run the recalculate_weights service first.
    await coordinator.async_refresh()
    coordinator.async_start_background_jobs()
    entry.async_on_unload(coordinator.async_stop_background_jobs)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    def _targets(call: ServiceCall) -> list[HaeoForecastCoordinator]:
        target_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if target_entry_id:
            if target_entry_id not in hass.data.get(DOMAIN, {}):
                raise HomeAssistantError(
                    f"HAEO Klima Forecast: config entry ID '{target_entry_id}' is not "
                    "(or no longer) known. The integration was likely set up again "
                    "since then and received a new entry ID - please reselect the "
                    "climate system in the service call, or leave the field empty "
                    "to address all systems."
                )
            return [hass.data[DOMAIN][target_entry_id]]
        return list(hass.data.get(DOMAIN, {}).values())

    async def _handle_recalculate_weights(call: ServiceCall) -> None:
        for target in _targets(call):
            await target.async_recalculate_weights()

    async def _handle_export_history(call: ServiceCall) -> dict:
        return {"files": [await target.async_export_history() for target in _targets(call)]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALCULATE_WEIGHTS,
        _handle_recalculate_weights,
        schema=SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_HISTORY,
        _handle_export_history,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_RECALCULATE_WEIGHTS)
        hass.services.async_remove(DOMAIN, SERVICE_EXPORT_HISTORY)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
