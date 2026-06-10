"""Report Store — persists cleaning and vendor service reports.

Stores structured reports from cleaners (post-cleaning photos,
checklist) and vendors (repair photos, cost, description). Reports
are saved to Redis with property and booking indexes for retrieval.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ReportType(StrEnum):
    """Types of service reports."""

    CLEANING = "cleaning"
    VENDOR_REPAIR = "vendor_repair"
    INSPECTION = "inspection"
    MAINTENANCE = "maintenance"


class ReportStatus(StrEnum):
    """Report lifecycle statuses."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class ServiceReport:
    """A structured report from a cleaner or vendor.

    Attributes:
        report_id: Unique report identifier.
        report_type: Type of service performed.
        status: Current report status.
        property_id: Property serviced.
        booking_id: Associated booking.
        session_id: Ops session that triggered this.
        contact_id: Who performed the service.
        contact_name: Name of service provider.
        started_at: When service started.
        completed_at: When service finished.
        duration_minutes: Time spent.
        checklist: Completed checklist items.
        photos: URLs of submitted photos.
        notes: Free-text notes from provider.
        cost_amount: Cost charged (vendors only).
        cost_currency: Currency code.
        issues_found: Problems discovered during service.
        quality_score: Auto-calculated quality score (0-100).
        approved_by: Who approved (PM/owner/auto).
        created_at: Report creation timestamp.
    """

    report_id: str = ""
    report_type: str = ReportType.CLEANING
    status: str = ReportStatus.PENDING
    property_id: str = ""
    booking_id: str = ""
    session_id: str = ""
    contact_id: str = ""
    contact_name: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_minutes: int = 0
    checklist: list[dict[str, Any]] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    notes: str = ""
    cost_amount: float = 0.0
    cost_currency: str = "EUR"
    issues_found: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    approved_by: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceReport:
        """Deserialize from dict.

        Args:
            data: Report dict.

        Returns:
            ServiceReport instance.
        """
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })


class ReportStore:
    """Redis-backed store for cleaning and vendor reports.

    Key structure:
        brain:report:{report_id}             → Report JSON
        brain:report:property:{property_id}  → Sorted set by time
        brain:report:booking:{booking_id}    → Set of report IDs
        brain:report:contact:{contact_id}    → Sorted set by time

    Args:
        redis_url: Redis connection URL.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = "brain:report:"

    def _key(self, *parts: str) -> str:
        """Build a Redis key."""
        return self._prefix + ":".join(parts)

    async def create_report(
        self,
        report_type: str,
        property_id: str,
        booking_id: str,
        contact_id: str,
        contact_name: str,
        session_id: str = "",
    ) -> ServiceReport:
        """Create a new pending report.

        Args:
            report_type: Type of service.
            property_id: Property ID.
            booking_id: Booking ID.
            contact_id: Service provider ID.
            contact_name: Provider name.
            session_id: Originating ops session.

        Returns:
            Created ServiceReport.
        """
        now = datetime.now(timezone.utc).isoformat()
        report = ServiceReport(
            report_id=str(uuid.uuid4())[:12],
            report_type=report_type,
            status=ReportStatus.PENDING,
            property_id=property_id,
            booking_id=booking_id,
            session_id=session_id,
            contact_id=contact_id,
            contact_name=contact_name,
            started_at=now,
            created_at=now,
        )
        await self._save(report)
        logger.info("Created report %s for %s", report.report_id, property_id)
        return report

    async def submit_report(
        self,
        report_id: str,
        photos: list[str] | None = None,
        notes: str = "",
        checklist: list[dict[str, Any]] | None = None,
        cost_amount: float = 0.0,
        issues_found: list[str] | None = None,
    ) -> ServiceReport | None:
        """Submit a completed report with photos and details.

        Args:
            report_id: Report to submit.
            photos: Photo URLs.
            notes: Provider notes.
            checklist: Completed checklist items.
            cost_amount: Cost charged.
            issues_found: Problems discovered.

        Returns:
            Updated report or None if not found.
        """
        report = await self.get_report(report_id)
        if not report:
            return None

        now = datetime.now(timezone.utc).isoformat()
        report.status = ReportStatus.SUBMITTED
        report.completed_at = now
        report.photos = photos or []
        report.notes = notes
        report.checklist = checklist or []
        report.cost_amount = cost_amount
        report.issues_found = issues_found or []
        report.duration_minutes = _calc_duration(report.started_at, now)
        report.quality_score = _calc_quality(report)

        await self._save(report)
        logger.info("Report %s submitted (score: %.0f)", report_id, report.quality_score)
        return report

    async def approve_report(
        self,
        report_id: str,
        approved_by: str = "auto",
    ) -> ServiceReport | None:
        """Approve a submitted report.

        Args:
            report_id: Report to approve.
            approved_by: Who approved.

        Returns:
            Updated report or None.
        """
        report = await self.get_report(report_id)
        if not report:
            return None

        report.status = ReportStatus.APPROVED
        report.approved_by = approved_by
        await self._save(report)
        return report

    async def get_report(self, report_id: str) -> ServiceReport | None:
        """Get a report by ID.

        Args:
            report_id: Report identifier.

        Returns:
            ServiceReport or None.
        """
        raw = await self._redis.get(self._key(report_id))
        if raw:
            return ServiceReport.from_dict(json.loads(raw))
        return None

    async def get_by_property(
        self,
        property_id: str,
        limit: int = 20,
    ) -> list[ServiceReport]:
        """Get reports for a property.

        Args:
            property_id: Property identifier.
            limit: Max reports to return.

        Returns:
            Reports in reverse chronological order.
        """
        ids = await self._redis.zrevrange(
            self._key("property", property_id), 0, limit - 1,
        )
        return await self._get_many(ids)

    async def get_by_booking(
        self,
        booking_id: str,
    ) -> list[ServiceReport]:
        """Get reports for a booking.

        Args:
            booking_id: Booking identifier.

        Returns:
            Reports for this booking.
        """
        ids = await self._redis.smembers(
            self._key("booking", booking_id),
        )
        return await self._get_many(list(ids))

    async def get_by_contact(
        self,
        contact_id: str,
        limit: int = 20,
    ) -> list[ServiceReport]:
        """Get reports by a specific contact/cleaner.

        Args:
            contact_id: Contact identifier.
            limit: Max reports.

        Returns:
            Reports by this contact.
        """
        ids = await self._redis.zrevrange(
            self._key("contact", contact_id), 0, limit - 1,
        )
        return await self._get_many(ids)

    async def close(self) -> None:
        """Close Redis connection."""
        await self._redis.close()

    # ── Internal ──────────────────────────────────────────────────────

    async def _save(self, report: ServiceReport) -> None:
        """Save report and update indexes.

        Args:
            report: Report to persist.
        """
        pipe = self._redis.pipeline()
        data = json.dumps(report.to_dict())
        pipe.set(self._key(report.report_id), data)

        ts = _timestamp_score(report.created_at)
        pipe.zadd(
            self._key("property", report.property_id),
            {report.report_id: ts},
        )
        pipe.sadd(
            self._key("booking", report.booking_id),
            report.report_id,
        )
        pipe.zadd(
            self._key("contact", report.contact_id),
            {report.report_id: ts},
        )
        await pipe.execute()

    async def _get_many(
        self, ids: list[str],
    ) -> list[ServiceReport]:
        """Fetch multiple reports by ID.

        Args:
            ids: Report IDs.

        Returns:
            Found reports.
        """
        reports: list[ServiceReport] = []
        for rid in ids:
            report = await self.get_report(rid)
            if report:
                reports.append(report)
        return reports


# ── Helpers ───────────────────────────────────────────────────────── #


def _calc_duration(started: str, completed: str) -> int:
    """Calculate duration in minutes between two ISO timestamps.

    Args:
        started: Start ISO timestamp.
        completed: End ISO timestamp.

    Returns:
        Duration in minutes.
    """
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = datetime.fromisoformat(completed)
        return max(0, int((end_dt - start_dt).total_seconds() / 60))
    except (ValueError, TypeError):
        return 0


def _calc_quality(report: ServiceReport) -> float:
    """Calculate quality score (0-100) for a report.

    Based on: photos submitted, checklist completed, no issues.

    Args:
        report: Submitted report.

    Returns:
        Quality score.
    """
    score = 50.0

    if report.photos:
        score += min(20.0, len(report.photos) * 5.0)

    if report.checklist:
        completed = sum(
            1 for item in report.checklist if item.get("done")
        )
        total = len(report.checklist)
        if total > 0:
            score += 20.0 * (completed / total)

    if not report.issues_found:
        score += 10.0
    else:
        score -= min(20.0, len(report.issues_found) * 5.0)

    return max(0.0, min(100.0, score))


def _timestamp_score(iso_str: str) -> float:
    """Convert ISO timestamp to float for Redis sorted sets.

    Args:
        iso_str: ISO timestamp.

    Returns:
        Unix timestamp as float.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0
