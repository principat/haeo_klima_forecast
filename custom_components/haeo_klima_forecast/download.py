"""HTTP endpoint that serves the exported history as a CSV download.

A button cannot start a browser download, so the export notification links to
this view with a short-lived signed URL (works without a bearer token).
"""
from __future__ import annotations

from datetime import timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN
from .history_store import rows_to_csv

DOWNLOAD_URL = f"/api/{DOMAIN}/export/{{entry_id}}"
DOWNLOAD_LINK_VALIDITY = timedelta(minutes=10)


def signed_download_path(hass: HomeAssistant, entry_id: str) -> str:
    """Relative, time-limited URL under which the CSV of this entry can be downloaded."""
    return async_sign_path(hass, DOWNLOAD_URL.format(entry_id=entry_id), DOWNLOAD_LINK_VALIDITY)


class HaeoExportDownloadView(HomeAssistantView):
    url = DOWNLOAD_URL
    name = f"api:{DOMAIN}:export"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = self._hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            return web.Response(status=404, text="Unknown climate system")
        rows = await coordinator.history_store.async_load()
        filename = f"{slugify(coordinator.entry.title)}_history.csv"
        return web.Response(
            body=rows_to_csv(rows).encode("utf-8"),
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
