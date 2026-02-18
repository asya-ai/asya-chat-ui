from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anyio
from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo

from app.services.tools.registry import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class TimeToolContext:
    org_id: str


def _resolve_timezone(
    *,
    timezone_name: str | None,
    city: str | None,
    country: str | None,
    latitude: float | None,
    longitude: float | None,
) -> tuple[str | None, dict[str, Any]]:
    if timezone_name:
        return timezone_name, {"source": "timezone"}

    if latitude is not None and longitude is not None:
        finder = TimezoneFinder()
        name = finder.timezone_at(lat=latitude, lng=longitude)
        return name, {"source": "coordinates", "lat": latitude, "lon": longitude}

    if city or country:
        parts = [item for item in [city, country] if item]
        query = ", ".join(parts)
        geocoder = Nominatim(user_agent="chatui/1.0")
        location = geocoder.geocode(query)
        if not location:
            return None, {"source": "geocode", "query": query, "error": "Not found"}
        finder = TimezoneFinder()
        name = finder.timezone_at(lat=location.latitude, lng=location.longitude)
        return (
            name,
            {
                "source": "geocode",
                "query": query,
                "lat": location.latitude,
                "lon": location.longitude,
            },
        )

    return None, {"error": "No location provided"}


async def get_time(
    context: TimeToolContext,
    *,
    timezone_name: str | None = None,
    city: str | None = None,
    country: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> ToolResult:
    def _run() -> ToolResult:
        tz_name, meta = _resolve_timezone(
            timezone_name=timezone_name,
            city=city,
            country=country,
            latitude=latitude,
            longitude=longitude,
        )
        if not tz_name:
            return ToolResult(name="get_time", output={"error": "Timezone not found", **meta})
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:
            logger.info("Invalid timezone %s: %s", tz_name, exc)
            return ToolResult(
                name="get_time",
                output={"error": "Invalid timezone", "timezone": tz_name},
            )
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)
        offset_minutes = int(now_local.utcoffset().total_seconds() // 60)
        return ToolResult(
            name="get_time",
            output={
                "timezone": tz_name,
                "local_time": now_local.isoformat(),
                "utc_time": now_utc.isoformat(),
                "offset_minutes": offset_minutes,
                **meta,
            },
        )

    return await anyio.to_thread.run_sync(_run)
