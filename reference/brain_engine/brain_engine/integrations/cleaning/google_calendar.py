"""
Google Calendar API integration for scheduling cleaning events and turnovers.

Uses the Google Calendar REST API with service-account or OAuth credentials.
Docs: https://developers.google.com/calendar/api/v3/reference
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True)
class CalendarEvent:
    """A Google Calendar event."""

    event_id: str
    title: str
    start: str
    end: str
    description: str | None = None
    status: str = "confirmed"
    html_link: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class GoogleCalendarError(Exception):
    """Raised when a Google Calendar API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GoogleCalendarClient:
    """
    Async client for the Google Calendar API.

    Expects a pre-obtained OAuth2 access token (refreshed externally).

    Usage::

        async with GoogleCalendarClient(
            access_token="ya29...", calendar_id="primary"
        ) as cal:
            event = await cal.create_event(
                "Cleaning - Unit 4A",
                start=datetime(2025, 3, 15, 11, 0),
                end=datetime(2025, 3, 15, 14, 0),
                description="Deep clean after checkout",
            )
            events = await cal.get_events(date(2025, 3, 15))
            await cal.cancel_event(event.event_id)
    """

    def __init__(
        self,
        access_token: str,
        *,
        calendar_id: str = "primary",
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._access_token = access_token
        self._calendar_id = calendar_id
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> GoogleCalendarClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def update_token(self, access_token: str) -> None:
        """Update the OAuth2 access token (e.g. after a refresh)."""
        self._access_token = access_token
        self._client.headers["Authorization"] = f"Bearer {access_token}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
        *,
        timezone: str = "UTC",
        attendees: list[str] | None = None,
    ) -> CalendarEvent:
        """Create a new calendar event.

        Args:
            title: Event summary / title.
            start: Start datetime.
            end: End datetime.
            description: Optional description body.
            timezone: IANA timezone identifier.
            attendees: Optional list of attendee email addresses.
        """
        payload: dict[str, Any] = {
            "summary": title,
            "start": {
                "dateTime": start.isoformat(),
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end.isoformat(),
                "timeZone": timezone,
            },
        }
        if description:
            payload["description"] = description
        if attendees:
            payload["attendees"] = [{"email": e} for e in attendees]

        data = await self._post(
            f"/calendars/{self._calendar_id}/events", payload
        )
        return self._parse_event(data)

    async def cancel_event(self, event_id: str) -> CalendarEvent:
        """Cancel (delete) a calendar event.

        Args:
            event_id: The Google Calendar event ID.
        """
        await self._delete(
            f"/calendars/{self._calendar_id}/events/{event_id}"
        )
        return CalendarEvent(
            event_id=event_id,
            title="",
            start="",
            end="",
            status="cancelled",
        )

    async def get_events(
        self,
        target_date: date,
        *,
        timezone: str = "UTC",
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """List events for a specific date.

        Args:
            target_date: The date to query.
            timezone: IANA timezone for the query window.
            max_results: Max events to return.
        """
        time_min = datetime(
            target_date.year, target_date.month, target_date.day, 0, 0, 0
        ).isoformat() + "Z"
        time_max = datetime(
            target_date.year, target_date.month, target_date.day, 23, 59, 59
        ).isoformat() + "Z"

        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": timezone,
            "maxResults": str(max_results),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        data = await self._get(
            f"/calendars/{self._calendar_id}/events", params=params
        )
        items: list[dict[str, Any]] = data.get("items", [])
        return [self._parse_event(item) for item in items]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_event(data: dict[str, Any]) -> CalendarEvent:
        start = data.get("start", {})
        end = data.get("end", {})
        return CalendarEvent(
            event_id=data.get("id", ""),
            title=data.get("summary", ""),
            start=start.get("dateTime", start.get("date", "")),
            end=end.get("dateTime", end.get("date", "")),
            description=data.get("description"),
            status=data.get("status", "confirmed"),
            html_link=data.get("htmlLink"),
            raw=data,
        )

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("GCal GET %s failed: %s", path, exc.response.text)
            raise GoogleCalendarError(
                f"API error on GET {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("GCal GET %s network error: %s", path, exc)
            raise GoogleCalendarError(f"Network error on GET {path}: {exc}") from exc

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("GCal POST %s failed: %s", path, exc.response.text)
            raise GoogleCalendarError(
                f"API error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("GCal POST %s network error: %s", path, exc)
            raise GoogleCalendarError(f"Network error on POST {path}: {exc}") from exc

    async def _delete(self, path: str) -> None:
        try:
            response = await self._client.delete(path)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("GCal DELETE %s failed: %s", path, exc.response.text)
            raise GoogleCalendarError(
                f"API error on DELETE {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("GCal DELETE %s network error: %s", path, exc)
            raise GoogleCalendarError(
                f"Network error on DELETE {path}: {exc}"
            ) from exc
