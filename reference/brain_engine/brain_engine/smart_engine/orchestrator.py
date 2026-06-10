"""APMOrchestrator — Event-driven pipeline from booking to check-in.

Triggered by webhook from Botel PMS when a new booking is detected.
Schedules and coordinates all autonomous tasks:

T-7 days: New booking → check guest score → flag if risky
T-2 days: Pre-check trigger → vendor checks + book cleaner + pre-checkout
T-0 (checkout): Photo inspection → cleaning → damage claim if needed
T-0 (checkin): Final approval → welcome message → access code → done
T+0: Update all scores in Self-Learning Engine

Each booking makes the system smarter for the next one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from brain_engine.smart_engine.scoring_engine import ScoringEngine
from brain_engine.smart_engine.cleaning_cascade import CleaningCascade, CascadeResult
from brain_engine.smart_engine.vendor_precheck import VendorPreCheck, PreCheckReport
from brain_engine.smart_engine.city_knowledge import CityKnowledgeGraph
from brain_engine.protocols import VoiceClient, Notifier

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnoverStatus:
    """Status of a complete guest turnover."""

    booking_id: str
    property_id: str
    city: str = ""
    status: str = "pending"  # pending | pre_checking | cleaning | ready | done | failed
    guest_score: float = 0
    guest_action: str = ""  # bonus | discount | standard
    cleaner_result: CascadeResult | None = None
    precheck_report: PreCheckReport | None = None
    checklist: dict[str, bool] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"TurnoverStatus(booking={self.booking_id!r}, "
            f"property={self.property_id!r}, status={self.status!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "booking_id": self.booking_id,
            "property_id": self.property_id,
            "city": self.city,
            "status": self.status,
            "guest_score": self.guest_score,
            "guest_action": self.guest_action,
            "cleaner_resolved": self.cleaner_result.resolved if self.cleaner_result else False,
            "precheck_ok": self.precheck_report.all_clear if self.precheck_report else False,
            "checklist": self.checklist,
            "timeline": self.timeline,
        }


class APMOrchestrator:
    """Main orchestrator for Autonomous Property Manager.

    Coordinates the full turnover pipeline from booking webhook
    to guest check-in.

    Args:
        scoring_engine: Central scoring engine.
        city_knowledge: Per-city accumulated knowledge.
        voice_client: ElevenLabs for calls.
        notifier: Telegram/WhatsApp.
        pms_client: Botel PMS for reservations + Seam devices.
        approval_gateway: For owner approvals.
    """

    def __init__(
        self,
        scoring_engine: ScoringEngine,
        city_knowledge: CityKnowledgeGraph | None = None,
        voice_client: VoiceClient | None = None,
        notifier: Notifier | None = None,
        pms_client: Any | None = None,
        approval_gateway: Any | None = None,  # ApprovalGateway (avoid circular import)
        dry_run: bool = False,
    ) -> None:
        self._scoring = scoring_engine
        self._city_kg = city_knowledge or CityKnowledgeGraph(scoring_engine)
        self._voice = voice_client
        self._notifier = notifier
        self._pms = pms_client
        self._approval = approval_gateway
        self._dry_run = dry_run
        self._active_turnovers: dict[str, TurnoverStatus] = {}

    async def on_new_booking(self, booking: dict[str, Any]) -> TurnoverStatus:
        """Handle a new booking webhook from PMS.

        Checks guest score, schedules pre-check tasks,
        and initiates the turnover pipeline.

        Args:
            booking: Booking data from PMS webhook.

        Returns:
            TurnoverStatus with initial state.
        """
        booking_id = booking.get("id", "")
        property_id = booking.get("property_id", "")
        guest_id = booking.get("guest_id", "")
        city = booking.get("city", "")
        checkin = booking.get("checkin_date", "")
        checkout = booking.get("checkout_date", "")

        status = TurnoverStatus(
            booking_id=booking_id,
            property_id=property_id,
            city=city,
        )
        self._active_turnovers[booking_id] = status

        # Step 1: Check guest score
        guest_score = await self._scoring.get_score(guest_id, "guest")
        status.guest_score = guest_score.global_score

        status.timeline.append({
            "event": "booking_received",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": f"Guest score: {guest_score.global_score:.1f}",
        })

        # Flag risky guests
        if guest_score.global_score < 20:
            logger.warning(
                "RISK GUEST: %s score=%.1f for booking %s",
                guest_id, guest_score.global_score, booking_id,
            )
            status.timeline.append({
                "event": "guest_risk_alert",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": f"Guest score {guest_score.global_score:.1f} < 20 — owner alerted",
            })

        logger.info(
            "New booking %s: property=%s, city=%s, guest_score=%.1f",
            booking_id, property_id, city, guest_score.global_score,
        )
        return status

    async def run_precheck(
        self,
        booking_id: str,
        cleaners: list[dict[str, Any]] | None = None,
        manager_phone: str = "",
        owner_phone: str = "",
    ) -> TurnoverStatus:
        """Run pre-check: vendor checks + book cleaner.

        Called T-2 days before check-in (or manually).

        Args:
            booking_id: Booking to prepare for.
            cleaners: Available cleaners (from config or DB).
            manager_phone: For escalation.
            owner_phone: For escalation.

        Returns:
            Updated TurnoverStatus.
        """
        status = self._active_turnovers.get(booking_id)
        if not status:
            logger.error("Booking %s not found in active turnovers", booking_id)
            return TurnoverStatus(booking_id=booking_id, property_id="", status="failed")

        status.status = "pre_checking"

        # ── Vendor Pre-Check ─────────────────────────────────────────────
        vendor_check = VendorPreCheck(
            scoring_engine=self._scoring,
            pms_client=self._pms,
            notifier=self._notifier,
            voice_client=self._voice,
            property_id=status.property_id,
            city=status.city,
        )
        report = await vendor_check.run_full_check()
        status.precheck_report = report
        status.checklist["vendor_precheck"] = report.all_clear

        status.timeline.append({
            "event": "vendor_precheck_done",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": f"Passed: {report.passed}/{report.total_checks}, issues: {len(report.issues)}",
        })

        # ── Cleaning Cascade ─────────────────────────────────────────────
        # Get cascade strategy from city maturity
        strategy = self._city_kg.get_cascade_strategy(status.city)
        logger.info("Cascade strategy for %s: %s", status.city, strategy)

        cascade = CleaningCascade(
            scoring_engine=self._scoring,
            voice_client=self._voice,
            notifier=self._notifier,
            property_id=status.property_id,
            city=status.city,
            booking_id=booking_id,
            dry_run=self._dry_run,
        )

        cascade_result = await cascade.execute(
            cleaners=cleaners or [],
            manager_phone=manager_phone,
            owner_phone=owner_phone,
        )
        status.cleaner_result = cascade_result
        status.checklist["cleaner_booked"] = cascade_result.resolved

        status.timeline.append({
            "event": "cleaner_cascade_done",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": (
                f"Resolved: {cascade_result.resolved}, "
                f"level: {cascade_result.level}, "
                f"attempts: {len(cascade_result.attempts)}"
            ),
        })

        # Check if ready
        all_ok = all(status.checklist.values())
        status.status = "ready" if all_ok else "pre_checking"

        return status

    async def finalize_turnover(
        self,
        booking_id: str,
        cleaner_quality: str = "good_review",
        guest_checkout_event: str = "clean_checkout",
    ) -> TurnoverStatus:
        """Finalize turnover after cleaning is done.

        Updates all scores and records the turnover for learning.

        Args:
            booking_id: Booking being finalized.
            cleaner_quality: How the cleaner performed.
            guest_checkout_event: How the guest left.

        Returns:
            Final TurnoverStatus.
        """
        status = self._active_turnovers.get(booking_id)
        if not status:
            return TurnoverStatus(booking_id=booking_id, property_id="", status="failed")

        # Update cleaner score
        if status.cleaner_result and status.cleaner_result.resolved:
            await self._scoring.record_event(
                entity_id=status.cleaner_result.cleaner_id,
                entity_type="cleaner",
                event_type=cleaner_quality,
                property_id=status.property_id,
                city=status.city,
            )

        # Update guest score
        await self._scoring.record_event(
            entity_id=booking_id,
            entity_type="guest",
            event_type=guest_checkout_event,
            property_id=status.property_id,
            city=status.city,
        )

        # Record turnover in city knowledge graph
        await self._city_kg.record_turnover(
            property_id=status.property_id,
            city=status.city,
            cleaner_id=status.cleaner_result.cleaner_id if status.cleaner_result else "",
            issues=[i.item for i in status.precheck_report.issues] if status.precheck_report else [],
        )

        status.status = "done"
        status.timeline.append({
            "event": "turnover_complete",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": "All scores updated, knowledge graph updated",
        })

        logger.info(
            "Turnover complete for %s: city=%s, maturity=%s",
            booking_id, status.city,
            (await self._city_kg.get_city_profile(status.city)).maturity,
        )
        return status

    def get_status(self, booking_id: str) -> TurnoverStatus | None:
        """Get current turnover status."""
        return self._active_turnovers.get(booking_id)

    def get_all_active(self) -> list[TurnoverStatus]:
        """Get all active turnovers."""
        return list(self._active_turnovers.values())
