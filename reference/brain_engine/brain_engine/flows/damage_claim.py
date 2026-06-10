"""Damage Claim Flow - Manages Airbnb damage claim submission.

Handles the entire damage claim lifecycle: detecting damage, collecting
evidence (photos, descriptions, receipts), building the claim document,
submitting to Airbnb, and tracking the claim status.

Critical rule: Claims must be submitted within 24 hours of checkout
and before the next guest checks in.

States: DAMAGE_DETECTED -> COLLECT_EVIDENCE -> BUILD_CLAIM -> SUBMIT -> TRACK -> DONE
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.streaming.event_types import EventType
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType, ApprovalStatus

logger = logging.getLogger(__name__)

# State constants
DAMAGE_DETECTED = "DAMAGE_DETECTED"
COLLECT_EVIDENCE = "COLLECT_EVIDENCE"
BUILD_CLAIM = "BUILD_CLAIM"
SUBMIT = "SUBMIT"
TRACK = "TRACK"
DONE = "DONE"

STATES = [DAMAGE_DETECTED, COLLECT_EVIDENCE, BUILD_CLAIM, SUBMIT, TRACK, DONE]

# Airbnb claim rules
CLAIM_DEADLINE_HOURS = 24
REQUIRED_EVIDENCE = [
    "before_photos",
    "after_photos",
    "damage_description",
    "repair_estimate",
    "host_statement",
]


class DamageClaimFlow:
    """Manages the Airbnb damage claim submission process.

    Orchestrates evidence collection, claim document construction,
    submission to Airbnb's Resolution Center, and status tracking.
    Enforces the 24-hour submission deadline and required documentation.

    Args:
        slot_manager: SlotManager instance with incident slots registered.
        emitter: AG-UI event emitter for streaming updates.
        session_id: Unique session identifier.
        checkout_time: When the guest checked out (for deadline calculation).
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        checkout_time: datetime | None = None,
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
        approval_gateway: ApprovalGateway | None = None,
        owner_id: str = "",
        property_id: str = "",
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id
        self._checkout_time = checkout_time or datetime.utcnow()
        self._recorder = event_recorder
        self._episodic = episodic
        self._approval = approval_gateway
        self._owner_id = owner_id
        self._property_id = property_id
        self._claim_deadline = self._checkout_time + timedelta(hours=CLAIM_DEADLINE_HOURS)

        transitions = [
            Transition(
                from_state=DAMAGE_DETECTED,
                to_state=COLLECT_EVIDENCE,
                condition=lambda ctx: self._damage_confirmed(),
            ),
            Transition(
                from_state=COLLECT_EVIDENCE,
                to_state=BUILD_CLAIM,
                condition=lambda ctx: self._evidence_complete(),
            ),
            Transition(
                from_state=BUILD_CLAIM,
                to_state=SUBMIT,
                condition=lambda ctx: self._claim_ready(),
            ),
            Transition(
                from_state=SUBMIT,
                to_state=TRACK,
                condition=lambda ctx: self._claim_submitted(),
            ),
            Transition(from_state=TRACK, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=DAMAGE_DETECTED,
        )

    def _damage_confirmed(self) -> bool:
        """Check if damage has been confirmed."""
        return self.slot_manager.get_value("damage_detected") is True

    def _evidence_complete(self) -> bool:
        """Check if all required evidence has been collected."""
        return self.slot_manager.get_value("evidence_complete") is True

    def _claim_ready(self) -> bool:
        """Check if the claim document is ready for submission."""
        required_slots = ["damage_description", "repair_estimate", "host_statement"]
        return all(
            self.slot_manager.get_value(slot) is not None
            for slot in required_slots
        )

    def _claim_submitted(self) -> bool:
        """Check if the claim has been submitted."""
        return self.slot_manager.get_value("claim_submitted") is True

    def _hours_until_deadline(self) -> float:
        """Calculate hours remaining until the claim deadline."""
        remaining = self._claim_deadline - datetime.utcnow()
        return max(0.0, remaining.total_seconds() / 3600)

    def _check_missing_evidence(self) -> list[str]:
        """Return list of missing evidence items."""
        missing = []
        if not self.slot_manager.get_value("photos_received"):
            missing.append("after_photos")
        if self.slot_manager.get_value("photos_before_count", 0) == 0:
            missing.append("before_photos")
        if not self.slot_manager.get_value("damage_description"):
            missing.append("damage_description")
        if not self.slot_manager.get_value("repair_estimate"):
            missing.append("repair_estimate")
        if not self.slot_manager.get_value("host_statement"):
            missing.append("host_statement")
        return missing

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the damage claim flow, yielding AG-UI events.

        Yields:
            AGUIEvent objects for each stage of the claim process.
        """
        flow_name = "damage_claim"
        hours_left = self._hours_until_deadline()

        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)

        if self._episodic:
            await self._episodic.add_episode(
                event="damage_claim_started",
                content=f"Damage claim flow initiated. Deadline: {hours_left:.1f}h remaining",
                metadata={"hours_left": hours_left, "deadline": self._claim_deadline.isoformat()},
            )

        # --- DAMAGE_DETECTED ---
        if not self._damage_confirmed():
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Damage claim flow initiated but damage has not been confirmed yet. "
                "Please complete the photo inspection flow first."
            )
            yield self.emitter.text_message_end()
            return

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Damage confirmed. Initiating claim process.\n"
            f"DEADLINE: {hours_left:.1f} hours remaining to submit claim "
            f"(deadline: {self._claim_deadline.strftime('%Y-%m-%d %H:%M UTC')}).\n"
            f"All claims must be submitted within 24 hours of checkout "
            f"and before the next guest checks in."
        )
        yield self.emitter.text_message_end()

        self.slot_manager.set_slot("claim_deadline", self._claim_deadline.isoformat())
        yield self.emitter.slot_filled("claim_deadline", self._claim_deadline.isoformat())

        # Transition to COLLECT_EVIDENCE
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=COLLECT_EVIDENCE)
        yield self.emitter.flow_state_changed(flow_name, old_state, COLLECT_EVIDENCE)

        # --- COLLECT_EVIDENCE ---
        missing = self._check_missing_evidence()

        if self._episodic:
            await self._episodic.add_episode(
                event="evidence_collection_started",
                content=f"Collecting evidence. Missing: {', '.join(missing) if missing else 'none'}",
                metadata={"missing_items": missing},
            )

        if missing:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Collecting evidence for the claim. Missing items:\n"
                + "\n".join(f"  - {item}" for item in missing)
            )
            yield self.emitter.text_message_end()

            # Request each missing piece of evidence
            for item in missing:
                prompt_map = {
                    "before_photos": "Please provide before-checkout photos of the property.",
                    "after_photos": "Please provide after-checkout photos showing damage.",
                    "damage_description": "Please provide a detailed description of the damage.",
                    "repair_estimate": "Please provide a repair estimate or quote.",
                    "host_statement": "Please write a host statement describing the incident.",
                }
                yield self.emitter.slot_requested(
                    item,
                    prompt_map.get(item, f"Please provide: {item}")
                )

            if not self._evidence_complete():
                return

        self.slot_manager.set_slot("evidence_complete", True)
        yield self.emitter.slot_filled("evidence_complete", True)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "All required evidence has been collected:\n"
            "  - Before photos: on file\n"
            "  - After photos: received\n"
            "  - Damage description: documented\n"
            "  - Repair estimate: obtained\n"
            "  - Host statement: written"
        )
        yield self.emitter.text_message_end()

        # Transition to BUILD_CLAIM
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=BUILD_CLAIM)
        yield self.emitter.flow_state_changed(flow_name, old_state, BUILD_CLAIM)

        # --- BUILD_CLAIM ---
        if self._episodic:
            await self._episodic.add_episode(
                event="evidence_complete",
                content="All required evidence collected. Building claim document",
                metadata={"state": BUILD_CLAIM},
            )

        yield self.emitter.tool_call_start("build_claim_document")

        damage_desc = self.slot_manager.get_value("damage_description", "")
        repair_est = self.slot_manager.get_value("repair_estimate", 0)
        replacement = self.slot_manager.get_value("replacement_cost", 0)
        severity = self.slot_manager.get_value("damage_severity", 0)
        items = self.slot_manager.get_value("damage_items", [])
        guest_name = self.slot_manager.get_value("guest_name", "Unknown")
        booking_id = self.slot_manager.get_value("booking_id", "Unknown")

        claim_amount = max(float(repair_est or 0), float(replacement or 0))
        self.slot_manager.set_slot("claim_amount", claim_amount)
        yield self.emitter.slot_filled("claim_amount", claim_amount)

        yield self.emitter.tool_call_end(
            "build_claim_document",
            {"claim_amount": claim_amount, "items_count": len(items) if isinstance(items, list) else 0},
        )

        if self._episodic:
            await self._episodic.add_episode(
                event="claim_document_built",
                content=f"Claim for ${claim_amount:.2f}, guest {guest_name}, booking {booking_id}",
                metadata={
                    "claim_amount": claim_amount,
                    "guest": guest_name,
                    "booking_id": booking_id,
                    "severity": severity,
                    "items_count": len(items) if isinstance(items, list) else 0,
                },
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Claim document built:\n"
            f"  Guest: {guest_name}\n"
            f"  Booking: {booking_id}\n"
            f"  Damage Severity: {severity}/5\n"
            f"  Claim Amount: ${claim_amount:.2f}\n"
            f"  Items: {len(items) if isinstance(items, list) else 0} damaged items\n\n"
            f"Description: {damage_desc[:200]}{'...' if len(str(damage_desc)) > 200 else ''}"
        )
        yield self.emitter.text_message_end()

        # Transition to SUBMIT
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=SUBMIT)
        yield self.emitter.flow_state_changed(flow_name, old_state, SUBMIT)

        # --- SUBMIT ---
        # ── Human-in-the-Loop: Owner MUST approve damage claims ──────────
        if self._approval:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Requesting owner approval to submit damage claim "
                f"for ${claim_amount:.2f}..."
            )
            yield self.emitter.text_message_end()

            yield self.emitter.tool_call_start("request_owner_approval")

            approval_result = await self._approval.request_approval(
                action_type=ActionType.SUBMIT_DAMAGE_CLAIM,
                owner_id=self._owner_id,
                property_id=self._property_id,
                description=(
                    f"Damage detected: {damage_desc[:100]}. "
                    f"Claim amount: ${claim_amount:.2f}. "
                    f"Severity: {severity}/5. "
                    f"Guest: {guest_name}. "
                    f"Deadline: {self._hours_until_deadline():.1f}h remaining."
                ),
                proposed_action={
                    "claim_amount": claim_amount,
                    "severity": severity,
                    "items_count": len(items) if isinstance(items, list) else 0,
                    "guest_name": guest_name,
                },
                context={
                    "guest_name": guest_name,
                    "booking_id": booking_id,
                    "hours_until_deadline": self._hours_until_deadline(),
                },
                urgency=4,  # High urgency — deadline sensitive
            )

            yield self.emitter.tool_call_end(
                "request_owner_approval",
                {"status": approval_result.status.value},
            )

            if approval_result.status == ApprovalStatus.DENIED:
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "Owner DENIED the damage claim submission. "
                    "Claim will NOT be submitted. "
                    "Note: the 24h deadline still applies if you change your mind."
                )
                yield self.emitter.text_message_end()

                self.slot_manager.set_slot("claim_status", "owner_denied")
                yield self.emitter.slot_filled("claim_status", "owner_denied")

                old_state = self.state_machine.current_state
                self.state_machine.transition(to_state=DONE)
                yield self.emitter.flow_state_changed(flow_name, old_state, DONE)
                yield self.emitter.flow_completed(
                    flow_name,
                    {"submitted": False, "reason": "owner_denied", "claim_amount": claim_amount},
                )
                return

            approval_label = (
                "auto-approved" if approval_result.status == ApprovalStatus.AUTO_APPROVED
                else "approved"
            )
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Owner {approval_label} the damage claim. Proceeding with submission."
            )
            yield self.emitter.text_message_end()

        hours_left = self._hours_until_deadline()

        if hours_left <= 0:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "WARNING: The 24-hour claim deadline has passed. "
                "The claim may be denied. Submitting anyway for documentation purposes."
            )
            yield self.emitter.text_message_end()

        yield self.emitter.tool_call_start("submit_airbnb_claim")

        # Simulate claim submission
        claim_id = f"CLM-{self.session_id[:8].upper() or 'XXXX'}-{datetime.utcnow().strftime('%Y%m%d')}"

        self.slot_manager.set_slot("claim_id", claim_id)
        yield self.emitter.slot_filled("claim_id", claim_id)

        self.slot_manager.set_slot("claim_submitted", True)
        yield self.emitter.slot_filled("claim_submitted", True)

        self.slot_manager.set_slot("claim_status", "submitted")
        yield self.emitter.slot_filled("claim_status", "submitted")

        if self._episodic:
            await self._episodic.add_episode(
                event="claim_submitted",
                content=f"Claim {claim_id} submitted for ${claim_amount:.2f}. {hours_left:.1f}h before deadline",
                metadata={
                    "claim_id": claim_id,
                    "amount": claim_amount,
                    "hours_until_deadline": hours_left,
                    "within_deadline": hours_left > 0,
                },
            )

        yield self.emitter.tool_call_end(
            "submit_airbnb_claim",
            {"claim_id": claim_id, "status": "submitted"},
        )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Claim submitted successfully to Airbnb Resolution Center.\n"
            f"  Claim ID: {claim_id}\n"
            f"  Amount: ${claim_amount:.2f}\n"
            f"  Status: Submitted\n"
            f"  Time remaining before deadline: {hours_left:.1f} hours\n\n"
            f"Airbnb typically reviews claims within 3-5 business days."
        )
        yield self.emitter.text_message_end()

        # Transition to TRACK
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=TRACK)
        yield self.emitter.flow_state_changed(flow_name, old_state, TRACK)

        # --- TRACK ---
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Claim {claim_id} is now being tracked. "
            "You will be notified of any status updates from Airbnb. "
            "Current status: Under Review."
        )
        yield self.emitter.text_message_end()

        self.slot_manager.set_slot("claim_status", "under_review")
        yield self.emitter.slot_filled("claim_status", "under_review")

        if self._episodic:
            await self._episodic.add_episode(
                event="claim_under_review",
                content=f"Claim {claim_id} now under Airbnb review",
                metadata={"claim_id": claim_id, "status": "under_review"},
            )

        # Transition to DONE
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DONE)
        yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

        # ── Memory: Record claim submission and tracking ─────────────────
        if self._recorder:
            await self._recorder.record_incident_update(
                event="claim_under_review",
                details=f"Claim {claim_id} submitted for ${claim_amount:.2f}, now under review",
                claim_id=claim_id,
                within_deadline=hours_left > 0,
            )

        yield self.emitter.flow_completed(
            flow_name,
            {
                "claim_id": claim_id,
                "claim_amount": claim_amount,
                "status": "under_review",
                "submitted_within_deadline": hours_left > 0,
            },
        )

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal

    @property
    def deadline(self) -> datetime:
        return self._claim_deadline
