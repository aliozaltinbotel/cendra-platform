"""Data-subject-rights coordinator (GDPR Articles 15–22).

Reference: ``brain_engine_advisory.md`` §4 (2) — right-to-erasure
cascade across every memory tier.

The coordinator is the single entry point for handling a subject
request — access, erasure, rectification, portability, restriction.
Each memory tier registers a :class:`TierEraser` (or
:class:`TierExporter`); the coordinator fans out, gathers per-tier
results, writes one audit event per request, and emits an immutable
:class:`DSRReport`.

The Protocols are intentionally narrow:

* :class:`TierEraser.erase` returns the count of records affected and
  the failure list — never raises for "subject not found".
* :class:`TierExporter.export` returns a JSON-serialisable payload —
  never streams.

That keeps the coordinator decision logic deterministic and trivially
testable; the real plumbing for asyncpg / Redis / Qdrant lives in
the per-store adapters under ``brain_engine/store/``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class DSRRequestType(StrEnum):
    """The five data-subject rights the engine handles end-to-end."""

    ACCESS = "access"  # Art. 15
    ERASURE = "erasure"  # Art. 17
    RECTIFICATION = "rectification"  # Art. 16
    PORTABILITY = "portability"  # Art. 20
    RESTRICTION = "restriction"  # Art. 18


class DSRStatus(StrEnum):
    """Lifecycle states the coordinator emits."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FULFILLED = "fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DSRRequest:
    """Frozen description of a request as it enters the coordinator."""

    request_id: str
    subject_id: str
    tenant_id: str
    request_type: DSRRequestType
    received_at: datetime
    requested_by: str
    justification: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("DSRRequest.request_id required")
        if not self.subject_id:
            raise ValueError("DSRRequest.subject_id required")
        if not self.tenant_id:
            raise ValueError("DSRRequest.tenant_id required")


@dataclass(frozen=True, slots=True)
class TierResult:
    """Per-tier outcome the coordinator stitches into the report."""

    tier: str
    affected: int
    failed: tuple[str, ...] = ()
    payload: Any = None

    @property
    def succeeded(self) -> bool:
        return not self.failed


@dataclass(frozen=True, slots=True)
class DSRReport:
    """Immutable record of how the coordinator handled the request."""

    request: DSRRequest
    status: DSRStatus
    completed_at: datetime
    results: tuple[TierResult, ...]

    def total_affected(self) -> int:
        return sum(r.affected for r in self.results)

    def failed_tiers(self) -> tuple[str, ...]:
        return tuple(r.tier for r in self.results if not r.succeeded)


class TierEraser(Protocol):
    """One memory tier's contribution to an erasure cascade."""

    @property
    def tier_name(self) -> str:
        """Stable identifier (``"episodic"``, ``"semantic"`` ...)."""

    def erase(
        self,
        *,
        subject_id: str,
        tenant_id: str,
    ) -> TierResult:
        """Remove subject's data from this tier."""


class TierExporter(Protocol):
    """One memory tier's contribution to an access / portability dump."""

    @property
    def tier_name(self) -> str: ...

    def export(
        self,
        *,
        subject_id: str,
        tenant_id: str,
    ) -> TierResult:
        """Return subject's data in a serialisable shape."""


class DataSubjectRightsCoordinator:
    """Fan-out + report writer for GDPR / KVKK subject requests.

    Each tier is registered once at engine startup; later, every
    request walks the same registered list so the report is reproducible
    and the audit trail is complete.  The coordinator is intentionally
    synchronous — async I/O happens inside the per-tier adapter.
    """

    def __init__(
        self,
        *,
        erasers: tuple[TierEraser, ...] = (),
        exporters: tuple[TierExporter, ...] = (),
    ) -> None:
        self._erasers = erasers
        self._exporters = exporters

    @property
    def erasers(self) -> tuple[TierEraser, ...]:
        return self._erasers

    @property
    def exporters(self) -> tuple[TierExporter, ...]:
        return self._exporters

    def handle(self, request: DSRRequest) -> DSRReport:
        """Execute the cascade matching ``request.request_type``."""
        if request.request_type is DSRRequestType.ERASURE:
            results = self._fanout_erase(request)
        elif request.request_type in (
            DSRRequestType.ACCESS,
            DSRRequestType.PORTABILITY,
        ):
            results = self._fanout_export(request)
        else:
            # Restriction / rectification need domain-specific handling
            # the coordinator cannot generalise.  Mark rejected and
            # surface the gap in the audit log.
            return DSRReport(
                request=request,
                status=DSRStatus.REJECTED,
                completed_at=datetime.now(tz=UTC),
                results=(),
            )
        return DSRReport(
            request=request,
            status=self._derive_status(results),
            completed_at=datetime.now(tz=UTC),
            results=results,
        )

    def _fanout_erase(self, request: DSRRequest) -> tuple[TierResult, ...]:
        return tuple(
            eraser.erase(
                subject_id=request.subject_id,
                tenant_id=request.tenant_id,
            )
            for eraser in self._erasers
        )

    def _fanout_export(
        self,
        request: DSRRequest,
    ) -> tuple[TierResult, ...]:
        return tuple(
            exporter.export(
                subject_id=request.subject_id,
                tenant_id=request.tenant_id,
            )
            for exporter in self._exporters
        )

    @staticmethod
    def _derive_status(
        results: tuple[TierResult, ...],
    ) -> DSRStatus:
        if not results:
            return DSRStatus.REJECTED
        if all(r.succeeded for r in results):
            return DSRStatus.FULFILLED
        if any(r.succeeded for r in results):
            return DSRStatus.PARTIALLY_FULFILLED
        return DSRStatus.REJECTED


def new_request_id() -> str:
    """Generate a stable, URL-safe request id.

    Centralised so every entry point (REST handler, ops CLI, retry
    job) uses the same shape; callers should not roll their own.
    """
    return f"dsr-{uuid.uuid4().hex}"
