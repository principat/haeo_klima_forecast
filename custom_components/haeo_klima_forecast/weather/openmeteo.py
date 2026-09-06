"""Open-Meteo weather provider (forecast + archive/history, free, no API key)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .base import WeatherPoint, WeatherProvider

_LOGGER = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HOURLY_VARS = "temperature_2m,shortwave_radiation,wind_speed_10m,relative_humidity_2m"

# The Open-Meteo archive API only delivers reanalyzed (quality-checked) data
# after a delay of a few days. An end_date too close to "today" causes a
# 400 Bad Request. A 5-day buffer is sufficiently conservative.
ARCHIVE_DELAY_DAYS = 5


class OpenMeteoProvider(WeatherProvider):
    """Provider for api.open-meteo.com."""

    name = "openmeteo"

    async def async_get_forecast(self, hours: int) -> list[WeatherPoint]:
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": HOURLY_VARS,
            "forecast_days": max(1, (hours // 24) + 2),
            "timezone": "UTC",
        }
        data = await self._request(FORECAST_URL, params)
        return self._parse(data)[:hours]

    async def async_get_historical(
        self, start: datetime, end: datetime
    ) -> list[WeatherPoint]:
        latest_available = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_DELAY_DAYS)
        effective_end = min(end, latest_available)

        if start >= effective_end:
            _LOGGER.warning(
                "HAEO Klima Forecast: requested period (%s to %s) lies "
                "entirely within the Open-Meteo archive delay of %s days "
                "and cannot be queried - no weather data will be provided "
                "for this part.",
                start,
                end,
                ARCHIVE_DELAY_DAYS,
            )
            return []

        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": HOURLY_VARS,
            "start_date": start.date().isoformat(),
            "end_date": effective_end.date().isoformat(),
            "timezone": "UTC",
        }
        data = await self._request(ARCHIVE_URL, params)
        points = self._parse(data)
        return [p for p in points if start <= p.timestamp <= effective_end]

    async def _request(self, url: str, params: dict) -> dict:
        async with self.session.get(url, params=params) as resp:
            if resp.status >= 400:
                # On 400-level errors, Open-Meteo returns a JSON body with a
                # "reason" field - that helps debugging far more than the
                # bare HTTP status code.
                body_text = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"Open-Meteo error: {body_text}",
                    headers=resp.headers,
                )
            return await resp.json()

    @staticmethod
    def _parse(data: dict) -> list[WeatherPoint]:
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        rad = hourly.get("shortwave_radiation", [])
        wind = hourly.get("wind_speed_10m", [])
        hum = hourly.get("relative_humidity_2m", [])

        points: list[WeatherPoint] = []
        for i, t in enumerate(times):
            points.append(
                WeatherPoint(
                    timestamp=datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                    temperature_c=temps[i] if i < len(temps) else None,
                    shortwave_radiation=rad[i] if i < len(rad) else None,
                    wind_speed_ms=wind[i] if i < len(wind) else None,
                    humidity_pct=hum[i] if i < len(hum) else None,
                )
            )
        return points
