"""Flight tracking via AviationStack API.

Provides real-time flight status lookup for guest arrival coordination.
Used to adjust cleaning schedules and send proactive notifications
when flights are delayed.

Docs: https://aviationstack.com/documentation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AVIATIONSTACK_BASE_URL = "http://api.aviationstack.com/v1"


@dataclass(frozen=True)
class FlightInfo:
    """Current status of a tracked flight."""
    flight_number: str
    airline: str = ""
    departure_airport: str = ""
    arrival_airport: str = ""
    scheduled_arrival: str = ""
    estimated_arrival: str = ""
    status: str = "unknown"  # scheduled, active, landed, cancelled, incident, diverted
    delay_minutes: int = 0
    gate: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class FlightTrackerError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FlightTracker:
    """AviationStack flight status client.

    Usage::

        async with FlightTracker(api_key="...") as tracker:
            info = await tracker.track_flight("TK1234")
            print(info.status, info.delay_minutes)
    """

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=AVIATIONSTACK_BASE_URL,
            timeout=timeout,
        )

    async def track_flight(self, flight_number: str) -> FlightInfo:
        """Look up current status of a flight.

        Args:
            flight_number: IATA flight number (e.g. "TK1234").

        Returns:
            FlightInfo with current status, delays, and gate info.
        """
        try:
            response = await self._client.get(
                "/flights",
                params={
                    "access_key": self.api_key,
                    "flight_iata": flight_number.upper().replace(" ", ""),
                },
            )
            response.raise_for_status()
            data = response.json()

            flights = data.get("data", [])
            if not flights:
                return FlightInfo(
                    flight_number=flight_number,
                    status="not_found",
                )

            f = flights[0]
            dep = f.get("departure", {})
            arr = f.get("arrival", {})
            airline = f.get("airline", {})

            delay = arr.get("delay") or 0

            logger.info(
                "Flight %s: status=%s, delay=%dmin",
                flight_number, f.get("flight_status", "unknown"), delay,
            )

            return FlightInfo(
                flight_number=flight_number,
                airline=airline.get("name", ""),
                departure_airport=dep.get("airport", ""),
                arrival_airport=arr.get("airport", ""),
                scheduled_arrival=arr.get("scheduled", ""),
                estimated_arrival=arr.get("estimated", "") or arr.get("scheduled", ""),
                status=f.get("flight_status", "unknown"),
                delay_minutes=int(delay),
                gate=arr.get("gate", "") or "",
                raw=f,
            )
        except httpx.HTTPStatusError as exc:
            raise FlightTrackerError(
                f"Flight lookup failed: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc

    async def estimate_arrival_time(self, flight_number: str) -> datetime | None:
        """Get estimated arrival time as a datetime object."""
        info = await self.track_flight(flight_number)
        time_str = info.estimated_arrival or info.scheduled_arrival
        if not time_str:
            return None
        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    async def get_delay_info(self, flight_number: str) -> dict[str, Any]:
        """Get delay status summary."""
        info = await self.track_flight(flight_number)
        return {
            "flight_number": info.flight_number,
            "status": info.status,
            "delay_minutes": info.delay_minutes,
            "is_delayed": info.delay_minutes > 15,
            "is_cancelled": info.status == "cancelled",
            "estimated_arrival": info.estimated_arrival,
        }

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FlightTracker:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
