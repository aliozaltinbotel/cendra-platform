"""Google Maps Distance Matrix integration for route/traffic estimation.

Provides distance, duration, and traffic-aware ETA calculations
for coordinating guest arrivals and cleaner dispatches.

Docs: https://developers.google.com/maps/documentation/distance-matrix
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


@dataclass(frozen=True)
class RouteInfo:
    """Distance and duration between two points."""
    origin: str
    destination: str
    distance_km: float = 0.0
    distance_text: str = ""
    duration_minutes: float = 0.0
    duration_text: str = ""
    duration_in_traffic_minutes: float = 0.0
    duration_in_traffic_text: str = ""
    status: str = "OK"


@dataclass(frozen=True)
class ETAResult:
    """Estimated time of arrival with traffic conditions."""
    origin: str
    destination: str
    departure_time: datetime | None = None
    eta: datetime | None = None
    duration_minutes: float = 0.0
    traffic_condition: str = "unknown"  # light, moderate, heavy


class GoogleMapsError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoogleMapsClient:
    """Google Maps Distance Matrix client.

    Usage::

        async with GoogleMapsClient(api_key="...") as maps:
            route = await maps.get_route("Airport", "123 Marina Blvd")
            print(route.duration_in_traffic_text)
    """

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def get_route(
        self,
        origin: str,
        destination: str,
        departure_time: datetime | None = None,
    ) -> RouteInfo:
        """Get distance and duration between two locations.

        Args:
            origin: Starting address or coordinates.
            destination: Destination address or coordinates.
            departure_time: For traffic-aware estimates (defaults to now).

        Returns:
            RouteInfo with distance and duration data.
        """
        dep_ts = int((departure_time or datetime.now(timezone.utc)).timestamp())

        try:
            response = await self._client.get(
                DISTANCE_MATRIX_URL,
                params={
                    "origins": origin,
                    "destinations": destination,
                    "key": self.api_key,
                    "departure_time": dep_ts,
                    "traffic_model": "best_guess",
                },
            )
            response.raise_for_status()
            data = response.json()

            rows = data.get("rows", [])
            if not rows or not rows[0].get("elements"):
                return RouteInfo(origin=origin, destination=destination, status="NO_RESULTS")

            elem = rows[0]["elements"][0]
            status = elem.get("status", "UNKNOWN")
            if status != "OK":
                return RouteInfo(origin=origin, destination=destination, status=status)

            distance = elem.get("distance", {})
            duration = elem.get("duration", {})
            duration_traffic = elem.get("duration_in_traffic", duration)

            route = RouteInfo(
                origin=origin,
                destination=destination,
                distance_km=distance.get("value", 0) / 1000.0,
                distance_text=distance.get("text", ""),
                duration_minutes=duration.get("value", 0) / 60.0,
                duration_text=duration.get("text", ""),
                duration_in_traffic_minutes=duration_traffic.get("value", 0) / 60.0,
                duration_in_traffic_text=duration_traffic.get("text", ""),
                status="OK",
            )

            logger.info(
                "Route %s → %s: %s (%s in traffic)",
                origin[:30], destination[:30],
                route.duration_text, route.duration_in_traffic_text,
            )
            return route

        except httpx.HTTPStatusError as exc:
            raise GoogleMapsError(
                f"Distance Matrix API failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def estimate_eta(
        self,
        origin: str,
        destination: str,
    ) -> ETAResult:
        """Estimate arrival time from origin to destination with traffic."""
        now = datetime.now(timezone.utc)
        route = await self.get_route(origin, destination, departure_time=now)

        if route.status != "OK":
            return ETAResult(origin=origin, destination=destination)

        from datetime import timedelta
        eta = now + timedelta(minutes=route.duration_in_traffic_minutes)

        # Classify traffic
        ratio = route.duration_in_traffic_minutes / max(route.duration_minutes, 1)
        if ratio < 1.15:
            traffic = "light"
        elif ratio < 1.4:
            traffic = "moderate"
        else:
            traffic = "heavy"

        return ETAResult(
            origin=origin,
            destination=destination,
            departure_time=now,
            eta=eta,
            duration_minutes=route.duration_in_traffic_minutes,
            traffic_condition=traffic,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GoogleMapsClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
