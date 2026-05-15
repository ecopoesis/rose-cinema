from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, WeatherForecast]] = {}
_CACHE_TTL = 3600 * 4

_ZIPPOPOTAM_URL = "https://api.zippopotam.us/us/{zip_code}"
_NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
_HTTP_TIMEOUT = 10.0
_NWS_HEADERS = {"User-Agent": "(rose-cinema, github.com/ecopoesis/rose-cinema)"}


@dataclass
class WeatherForecast:
    location: str
    temperature: int
    temperature_unit: str
    short_forecast: str
    detailed_forecast: str
    wind_speed: str
    period_name: str


async def get_forecast(postal_code: str) -> WeatherForecast | None:
    now = time.monotonic()
    cached = _CACHE.get(postal_code)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        forecast = await _fetch_forecast(postal_code)
    except Exception:
        logger.exception("Failed to fetch weather for %s", postal_code)
        return None

    if forecast:
        _CACHE[postal_code] = (now, forecast)
    return forecast


async def _fetch_forecast(postal_code: str) -> WeatherForecast | None:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        zip_resp = await client.get(_ZIPPOPOTAM_URL.format(zip_code=postal_code))
        if zip_resp.status_code != 200:
            logger.warning("Zip lookup failed for %s: %d", postal_code, zip_resp.status_code)
            return None
        zip_data = zip_resp.json()
        place = zip_data.get("places", [{}])[0]
        lat = place.get("latitude")
        lon = place.get("longitude")
        location = place.get("place name", "")
        state = place.get("state abbreviation", place.get("state", ""))
        if location and state:
            location = f"{location}, {state}"
        if not lat or not lon:
            return None

        points_resp = await client.get(
            _NWS_POINTS_URL.format(lat=lat, lon=lon),
            headers=_NWS_HEADERS,
        )
        if points_resp.status_code != 200:
            logger.warning("NWS points failed for %s,%s: %d", lat, lon, points_resp.status_code)
            return None
        forecast_url = points_resp.json().get("properties", {}).get("forecast")
        if not forecast_url:
            return None

        forecast_resp = await client.get(forecast_url, headers=_NWS_HEADERS)
        if forecast_resp.status_code != 200:
            logger.warning("NWS forecast failed: %d", forecast_resp.status_code)
            return None

        periods = forecast_resp.json().get("properties", {}).get("periods", [])
        for period in periods:
            if period.get("isDaytime"):
                return WeatherForecast(
                    location=location,
                    temperature=period["temperature"],
                    temperature_unit=period["temperatureUnit"],
                    short_forecast=period["shortForecast"],
                    detailed_forecast=period["detailedForecast"],
                    wind_speed=period["windSpeed"],
                    period_name=period["name"],
                )

    return None
