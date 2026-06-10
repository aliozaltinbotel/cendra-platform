"""
Turno (TurnoverBnB) integration for cleaning service management.

Docs: https://turno.com/api-docs/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.turno.com/api/v1"


@dataclass(frozen=True)
class Cleaner:
    """A cleaner available on the Turno platform."""

    cleaner_id: str
    name: str
    rating: float | None = None
    phone: str | None = None
    email: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class CleaningJob:
    """A scheduled or completed cleaning job."""

    job_id: str
    cleaner_id: str
    property_id: str
    date: str
    status: str  # "scheduled", "in_progress", "completed", "cancelled"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class TurnoError(Exception):
    """Raised when a Turno API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TurnoClient:
    """
    Async client for the Turno (TurnoverBnB) API.

    Usage::

        async with TurnoClient(api_key="key_...") as turno:
            cleaners = await turno.get_available_cleaners(
                date(2025, 3, 15), "prop-123"
            )
            job = await turno.assign_cleaner(
                cleaners[0].cleaner_id, "prop-123", date(2025, 3, 15)
            )
            status = await turno.get_cleaning_status(job.job_id)
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> TurnoClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_cleaners(
        self, cleaning_date: date, property_id: str
    ) -> list[Cleaner]:
        """Find cleaners available for a specific date and property.

        Args:
            cleaning_date: The date cleaning is needed.
            property_id: Identifier of the property to clean.

        Returns:
            List of available :class:`Cleaner` objects.
        """
        params = {
            "date": cleaning_date.isoformat(),
            "property_id": property_id,
        }
        data = await self._get("/cleaners/available", params=params)
        cleaners_data: list[dict[str, Any]] = data.get("data", [])
        return [
            Cleaner(
                cleaner_id=c.get("id", ""),
                name=c.get("name", ""),
                rating=c.get("rating"),
                phone=c.get("phone"),
                email=c.get("email"),
                raw=c,
            )
            for c in cleaners_data
        ]

    async def assign_cleaner(
        self,
        cleaner_id: str,
        property_id: str,
        cleaning_date: date,
        *,
        notes: str | None = None,
    ) -> CleaningJob:
        """Assign a cleaner to a property for a specific date.

        Args:
            cleaner_id: ID of the cleaner to assign.
            property_id: Property to be cleaned.
            cleaning_date: Scheduled cleaning date.
            notes: Optional instructions for the cleaner.
        """
        payload: dict[str, Any] = {
            "cleaner_id": cleaner_id,
            "property_id": property_id,
            "date": cleaning_date.isoformat(),
        }
        if notes:
            payload["notes"] = notes

        data = await self._post("/jobs", payload)
        job = data.get("data", {})
        return CleaningJob(
            job_id=job.get("id", ""),
            cleaner_id=cleaner_id,
            property_id=property_id,
            date=cleaning_date.isoformat(),
            status=job.get("status", "scheduled"),
            raw=job,
        )

    async def get_cleaning_status(self, job_id: str) -> CleaningJob:
        """Get the status of a cleaning job.

        Args:
            job_id: The job identifier.
        """
        data = await self._get(f"/jobs/{job_id}")
        job = data.get("data", {})
        return CleaningJob(
            job_id=job_id,
            cleaner_id=job.get("cleaner_id", ""),
            property_id=job.get("property_id", ""),
            date=job.get("date", ""),
            status=job.get("status", "unknown"),
            raw=job,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Turno GET %s failed: %s", path, exc.response.text)
            raise TurnoError(
                f"API error on GET {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Turno GET %s network error: %s", path, exc)
            raise TurnoError(f"Network error on GET {path}: {exc}") from exc

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=json)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.HTTPStatusError as exc:
            logger.error("Turno POST %s failed: %s", path, exc.response.text)
            raise TurnoError(
                f"API error on POST {path}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Turno POST %s network error: %s", path, exc)
            raise TurnoError(f"Network error on POST {path}: {exc}") from exc
