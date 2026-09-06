"""DWD weather provider.

The Deutscher Wetterdienst itself does not offer a simple lat/lon JSON API
for private users. As a pragmatic, free access point, Bright Sky
(https://brightsky.dev) is used here, an open wrapper around the DWD open
data products (MOSMIX forecast + station observations). The data format of
the `WeatherPoint` objects is identical to all other providers, so the
weighting/forecast code stays unchanged.

If you'd rather connect a different DWD source (e.g. your own MOSMIX KMZ
parsing), only this file needs to be adapted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .base import WeatherPoint, WeatherProvider

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.brightsky.dev"

# Station observations are usually available nearly in real time, but
# depending on the station late reports can occur. A 1-day buffer prevents
# gaps at the current edge of the requested time period.
OBSERVATION_DELAY_DAYS = 1


class DwdProvider(WeatherProvider):
    """Provider for DWD data via Bright Sky."""

    name = "dwd"

    async def async_get_forecast(self, hours: int) -> list[WeatherPoint]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=hours + 1)
        params = {
            "lat": self.latitude,
            "lon": self.longitude,
            "date": now.date().isoformat(),
            "last_date": end.date().isoformat(),
        }
        data = await self._request(params)
        points = self._parse(data)
        return [p for p in points if p.timestamp >= now][:hours]

    async def async_get_historical(
        self, start: datetime, end: datetime
    ) -> list[WeatherPoint]:
        latest_available = datetime.now(timezone.utc) - timedelta(days=OBSERVATION_DELAY_DAYS)
        effective_end = min(end, latest_available)

        if start >= effective_end:
            _LOGGER.warning(
                "HAEO Klima Forecast: requested period (%s to %s) lies "
                "entirely within the Bright Sky/DWD reporting delay of "
                "%s day(s) and cannot be queried.",
                start,
                end,
                OBSERVATION_DELAY_DAYS,
            )
            return []

        params = {
            "lat": self.latitude,
            "lon": self.longitude,
            "date": start.date().isoformat(),
            "last_date": effective_end.date().isoformat(),
        }
        data = await self._request(params)
        points = self._parse(data)
        return [p for p in points if start <= p.timestamp <= effective_end]

    async def _request(self, params: dict) -> dict:
        async with self.session.get(f"{BASE_URL}/weather", params=params) as resp:
            if resp.status >= 400:
                body_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"Bright Sky/DWD error: {body_text}",
                    headers=resp.headers,
                )
            return await resp.json()

    @staticmethod
    def _parse(data: dict) -> list[WeatherPoint]:
        points: list[WeatherPoint] = []
        for entry in data.get("weather", []):
            ts = entry.get("timestamp")
            if ts is None:
                continue
            points.append(
                WeatherPoint(
                    timestamp=datetime.fromisoformat(ts.replace("Z", "+00:00")),
                    temperature_c=entry.get("temperature"),
                    shortwave_radiation=entry.get("solar_10") and entry["solar_10"] * 1000 / 10,
                    wind_speed_ms=(
                        entry["wind_speed"] / 3.6 if entry.get("wind_speed") is not None else None
                    ),
                    humidity_pct=entry.get("relative_humidity"),
                )
            )
        return points
