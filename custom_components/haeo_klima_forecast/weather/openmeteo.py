"""Open-Meteo Historical Weather API client.

Used exclusively for the training-data path (see history_store.py). The
forecast path no longer talks to any weather API directly - it consumes a
forecast-template entity instead (see forecast_template.py). Provider
plug-ability is therefore not needed here anymore: this is the one fixed
source for historical weather (see SPECIFICATION.md, section 1.7).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .base import WeatherPoint

_LOGGER = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"  # also serves the last ~92 days of past data

HOURLY_VARS = (
    "temperature_2m,wind_speed_10m,wind_direction_10m,"
    "relative_humidity_2m"
)

# The Open-Meteo archive API only delivers reanalyzed (quality-checked) data
# after a delay of a few days. An end_date too close to "today" causes a
# 400 Bad Request. A 5-day buffer is sufficiently conservative.
ARCHIVE_DELAY_DAYS = 5


class OpenMeteoHistoricalClient:
    """Fetches historical hourly weather data for one location."""

    def __init__(self, latitude: float, longitude: float, session: aiohttp.ClientSession) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.session = session

    async def async_get_historical(self, start: datetime, end: datetime) -> list[WeatherPoint]:
        """Archive API for the settled part, forecast API (past data) for the last days.

        The archive lags a few days behind; the most recent hours come from
        the forecast API's model analysis instead, so a fresh installation
        does not have to wait days until its newest hours become usable.
        """
        latest_available = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_DELAY_DAYS)
        by_time: dict[datetime, WeatherPoint] = {}

        if start < latest_available:
            data = await self._request(ARCHIVE_URL, self._params(start, min(end, latest_available)))
            by_time.update((p.timestamp, p) for p in self._parse(data))

        recent_start = max(start, latest_available)
        if recent_start <= end:
            try:
                data = await self._request(FORECAST_URL, self._params(recent_start, end))
                for p in self._parse(data):
                    by_time.setdefault(p.timestamp, p)  # archive wins where both exist
            except aiohttp.ClientError as err:
                _LOGGER.warning(
                    "HAEO Klima Forecast: no recent weather data from Open-Meteo (%s to %s): %s",
                    recent_start,
                    end,
                    err,
                )
        return [p for t, p in sorted(by_time.items()) if start <= t <= end]

    def _params(self, start: datetime, end: datetime) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "hourly": HOURLY_VARS,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "timezone": "UTC",
        }

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
        wind = hourly.get("wind_speed_10m", [])
        wind_dir = hourly.get("wind_direction_10m", [])
        hum = hourly.get("relative_humidity_2m", [])

        points: list[WeatherPoint] = []
        for i, t in enumerate(times):
            points.append(
                WeatherPoint(
                    timestamp=datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                    temperature_c=temps[i] if i < len(temps) else None,
                    wind_speed_ms=wind[i] if i < len(wind) else None,
                    wind_direction_deg=wind_dir[i] if i < len(wind_dir) else None,
                    humidity_pct=hum[i] if i < len(hum) else None,
                )
            )
        return points
