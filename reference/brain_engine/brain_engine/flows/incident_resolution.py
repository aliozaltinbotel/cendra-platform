"""Incident Resolution Flow - Master flow orchestrating the full incident lifecycle.

Coordinates the sub-flows (late checkout, cleaner coordination, photo inspection,
damage claim) into a unified incident resolution pipeline. Manages the handoff
between sub-flows and tracks overall incident status.

States cover the full lifecycle:
    INCIDENT_OPEN -> HANDLE_CHECKOUT -> DISPATCH_CLEANER ->
    INSPECT_PHOTOS -> PROCESS_CLAIM -> RESOLUTION -> DONE
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.streaming.event_types import EventType
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.cognitive_controller import CognitiveController
from brain_engine.memory.episodic_memory import EpisodicMemory

from brain_engine.flows.late_checkout import LateCheckoutFlow
from brain_engine.flows.cleaner_coordination_legacy import CleanerCoordinationFlow
from brain_engine.flows.photo_inspection import PhotoInspectionFlow
from brain_engine.flows.damage_claim import DamageClaimFlow
from brain_engine.integrations.vision.photo_comparator import PhotoComparator
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)

# State constants
INCIDENT_OPEN = "INCIDENT_OPEN"
HANDLE_CHECKOUT = "HANDLE_CHECKOUT"
DISPATCH_CLEANER = "DISPATCH_CLEANER"
INSPECT_PHOTOS = "INSPECT_PHOTOS"
PROCESS_CLAIM = "PROCESS_CLAIM"
RESOLUTION = "RESOLUTION"
DONE = "DONE"

STATES = [
    INCIDENT_OPEN,
    HANDLE_CHECKOUT,
    DISPATCH_CLEANER,
    INSPECT_PHOTOS,
    PROCESS_CLAIM,
    RESOLUTION,
    DONE,
]


class IncidentResolutionFlow:
    """Master flow that orchestrates the full incident resolution lifecycle.

    Manages the sequential execution of sub-flows:
    1. Late checkout handling (if applicable)
    2. Cleaner coordination and dispatch
    3. Photo inspection and damage analysis
    4. Damage claim submission (if damage detected)

    Each sub-flow can be skipped if not applicable to the current incident.

    Args:
        slot_manager: SlotManager instance with incident slots registered.
        emitter: AG-UI event emitter for streaming updates.
        session_id: Unique session identifier.
        skip_checkout: Whether to skip the late checkout sub-flow.
        skip_claim: Whether to skip the damage claim sub-flow.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        skip_checkout: bool = False,
        skip_claim: bool = False,
        photo_comparator: PhotoComparator | None = None,
        event_recorder: EventRecorder | None = None,
        cognitive: CognitiveController | None = None,
        episodic: EpisodicMemory | None = None,
        approval_gateway: ApprovalGateway | None = None,
        owner_id: str = "",
        property_id: str = "",
        ops_logger: OpsDecisionLogger | None = None,
        reservation_id: str | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self._skip_checkout = skip_checkout
        self._skip_claim = skip_claim
        self._photo_comparator = photo_comparator
        self._recorder = event_recorder
        self._cognitive = cognitive
        self._episodic = episodic
        self._approval = approval_gateway
        self._owner_id = owner_id
        self._property_id = property_id
        self._ops_logger = ops_logger
        self._reservation_id = reservation_id

        # Sub-flow instances (created lazily)
        self._late_checkout: LateCheckoutFlow | None = None
        self._cleaner: CleanerCoordinationFlow | None = None
        self._photo: PhotoInspectionFlow | None = None
        self._damage_claim: DamageClaimFlow | None = None

        # Sub-flow results
        self._sub_results: dict[str, dict[str, Any]] = {}

        transitions = [
            Transition(from_state=INCIDENT_OPEN, to_state=HANDLE_CHECKOUT),
            Transition(from_state=HANDLE_CHECKOUT, to_state=DISPATCH_CLEANER),
            Transition(from_state=DISPATCH_CLEANER, to_state=INSPECT_PHOTOS),
            Transition(from_state=INSPECT_PHOTOS, to_state=PROCESS_CLAIM),
            Transition(
                from_state=PROCESS_CLAIM,
                to_state=RESOLUTION,
            ),
            Transition(from_state=RESOLUTION, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=INCIDENT_OPEN,
        )

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the full incident resolution flow.

        Orchestrates sub-flows in sequence, passing data between them
        via the shared SlotManager. Each sub-flow's events are yielded
        to the caller, with master flow events interspersed.

        Yields:
            AGUIEvent objects from both master flow and sub-flows.
        """
        flow_name = "incident_resolution"
        incident_id = f"INC-{self.session_id.upper()}-{datetime.utcnow().strftime('%Y%m%d%H%M')}"

        # Set incident metadata
        self.slot_manager.set_slot("incident_id", incident_id)
        self.slot_manager.set_slot("incident_status", "open")
        self.slot_manager.set_slot("incident_created_at", datetime.utcnow().isoformat())

        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)
        yield self.emitter.slot_filled("incident_id", incident_id)
        yield self.emitter.slot_filled("incident_status", "open")

        if self._episodic:
            await self._episodic.add_episode(
                event="incident_opened",
                content=f"Incident {incident_id} opened",
                metadata={"incident_id": incident_id, "state": INCIDENT_OPEN},
            )

        # ── Memory: Record guest identification and incident start ──────────
        guest_name = self.slot_manager.get_value("guest_name", "Unknown")
        guest_phone = self.slot_manager.get_value("guest_phone")
        booking_id = self.slot_manager.get_value("booking_id", "")
        property_id = self.slot_manager.get_value("property_id", "")

        if self._recorder:
            await self._recorder.record_guest_identified(
                guest_name=guest_name,
                phone=guest_phone,
                booking_id=booking_id,
                property_id=property_id,
            )
            await self._recorder.record_incident_started(
                incident_type="incident_resolution",
                guest_name=guest_name,
                booking_id=booking_id,
                property_id=property_id,
            )

        if self._cognitive:
            await self._cognitive.process(
                event="incident_started",
                content=f"Incident {incident_id} opened for guest {guest_name}",
                metadata={"guest_name": guest_name, "booking_id": booking_id, "property_id": property_id},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Incident {incident_id} opened. Starting incident resolution pipeline.\n"
            f"Guest: {self.slot_manager.get_value('guest_name', 'Unknown')}\n"
            f"Property: {self.slot_manager.get_value('property_id', 'Unknown')}\n"
            f"Booking: {self.slot_manager.get_value('booking_id', 'Unknown')}"
        )
        yield self.emitter.text_message_end()

        # ================================================================
        # PHASE 1: HANDLE CHECKOUT
        # ================================================================
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=HANDLE_CHECKOUT)
        yield self.emitter.flow_state_changed(flow_name, old_state, HANDLE_CHECKOUT)

        self.slot_manager.set_slot("incident_status", "in_progress")
        yield self.emitter.slot_filled("incident_status", "in_progress")

        if self._skip_checkout:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "No late checkout request. Proceeding to cleaner dispatch."
            )
            yield self.emitter.text_message_end()
        else:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Phase 1: Handling late checkout request..."
            )
            yield self.emitter.text_message_end()

            self._late_checkout = LateCheckoutFlow(
                slot_manager=self.slot_manager,
                emitter=self.emitter,
                session_id=self.session_id,
                event_recorder=self._recorder,
                episodic=self._episodic,
                approval_gateway=self._approval,
                owner_id=self._owner_id,
                property_id=self._property_id,
            )

            async for event in self._late_checkout.run():
                yield event

            self._sub_results["late_checkout"] = {
                "completed": self._late_checkout.is_done,
                "state": self._late_checkout.current_state,
                "approved": self.slot_manager.get_value("late_checkout_approved"),
            }

            # ── Memory: Record late checkout result ─────────────────────────
            if self._recorder:
                approved = self.slot_manager.get_value("late_checkout_approved")
                checkout_time = self.slot_manager.get_value("john_checkout_time", "")
                fee = self.slot_manager.get_value("late_checkout_fee")
                if checkout_time:
                    await self._recorder.record_late_checkout_requested(checkout_time, fee)
                if approved is not None:
                    await self._recorder.record_late_checkout_decision(
                        approved=bool(approved),
                        reason=f"Fee: ${fee}" if fee else "",
                    )

        # ================================================================
        # PHASE 2: DISPATCH CLEANER
        # ================================================================
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DISPATCH_CLEANER)
        yield self.emitter.flow_state_changed(flow_name, old_state, DISPATCH_CLEANER)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Phase 2: Coordinating cleaning crew..."
        )
        yield self.emitter.text_message_end()

        self._cleaner = CleanerCoordinationFlow(
            slot_manager=self.slot_manager,
            emitter=self.emitter,
            session_id=self.session_id,
            event_recorder=self._recorder,
            episodic=self._episodic,
            approval_gateway=self._approval,
            owner_id=self._owner_id,
            property_id=self._property_id,
            ops_logger=self._ops_logger,
            reservation_id=self._reservation_id,
        )

        async for event in self._cleaner.run():
            yield event

        self._sub_results["cleaner_coordination"] = {
            "completed": self._cleaner.is_done,
            "state": self._cleaner.current_state,
            "cleaner": self.slot_manager.get_value("cleaner_name"),
        }

        # ── Memory: Record cleaner assignment ───────────────────────────
        if self._recorder:
            cleaner_name = self.slot_manager.get_value("cleaner_name", "")
            cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")
            arrival = self.slot_manager.get_value("cleaner_arrival_time", "TBD")
            if cleaner_name:
                await self._recorder.record_cleaner_assigned(cleaner_name, cleaner_phone, arrival)

        # ================================================================
        # PHASE 3: INSPECT PHOTOS
        # ================================================================
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=INSPECT_PHOTOS)
        yield self.emitter.flow_state_changed(flow_name, old_state, INSPECT_PHOTOS)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Phase 3: Photo inspection and damage analysis..."
        )
        yield self.emitter.text_message_end()

        self._photo = PhotoInspectionFlow(
            slot_manager=self.slot_manager,
            emitter=self.emitter,
            session_id=self.session_id,
            photo_comparator=self._photo_comparator,
            event_recorder=self._recorder,
            episodic=self._episodic,
        )

        async for event in self._photo.run():
            yield event

        self._sub_results["photo_inspection"] = {
            "completed": self._photo.is_done,
            "state": self._photo.current_state,
            "damage_detected": self.slot_manager.get_value("damage_detected"),
        }

        # ── Memory: Record photo inspection result ──────────────────────
        if self._recorder:
            before_count = self.slot_manager.get_value("photos_before_count", 0)
            after_count = self.slot_manager.get_value("photos_after_count", 0)
            await self._recorder.record_photos_received(before_count, after_count)

            damage_detected = self.slot_manager.get_value("damage_detected")
            if damage_detected:
                await self._recorder.record_damage_detected(
                    description=self.slot_manager.get_value("damage_description", ""),
                    severity=self.slot_manager.get_value("damage_severity", 1),
                    items=self.slot_manager.get_value("damage_items", []),
                    confidence=self.slot_manager.get_value("analysis_confidence"),
                )
            else:
                await self._recorder.record_no_damage()

        if self._cognitive and damage_detected:
            await self._cognitive.process(
                event="damage_detected",
                content=self.slot_manager.get_value("damage_description", ""),
                metadata={
                    "severity": self.slot_manager.get_value("damage_severity"),
                    "guest_id": self._recorder._current_guest_id if self._recorder else "",
                    "property_id": property_id,
                },
            )

        # ================================================================
        # PHASE 4: PROCESS DAMAGE CLAIM
        # ================================================================
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=PROCESS_CLAIM)
        yield self.emitter.flow_state_changed(flow_name, old_state, PROCESS_CLAIM)

        damage_detected = self.slot_manager.get_value("damage_detected")

        if self._skip_claim or not damage_detected:
            yield self.emitter.text_message_start()
            if not damage_detected:
                yield self.emitter.text_message_content(
                    "No damage detected during photo inspection. "
                    "Skipping damage claim process."
                )
            else:
                yield self.emitter.text_message_content(
                    "Damage claim processing skipped per configuration."
                )
            yield self.emitter.text_message_end()

            self._sub_results["damage_claim"] = {
                "completed": False,
                "skipped": True,
                "reason": "no_damage" if not damage_detected else "config_skip",
            }
        else:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Phase 4: Processing damage claim..."
            )
            yield self.emitter.text_message_end()

            self._damage_claim = DamageClaimFlow(
                slot_manager=self.slot_manager,
                emitter=self.emitter,
                session_id=self.session_id,
                event_recorder=self._recorder,
                episodic=self._episodic,
                approval_gateway=self._approval,
                owner_id=self._owner_id,
                property_id=self._property_id,
            )

            async for event in self._damage_claim.run():
                yield event

            self._sub_results["damage_claim"] = {
                "completed": self._damage_claim.is_done,
                "state": self._damage_claim.current_state,
                "claim_id": self.slot_manager.get_value("claim_id"),
                "claim_amount": self.slot_manager.get_value("claim_amount"),
            }

            # ── Memory: Record claim submission ─────────────────────────
            if self._recorder:
                claim_id = self.slot_manager.get_value("claim_id", "")
                claim_amount = self.slot_manager.get_value("claim_amount", 0)
                deadline = self.slot_manager.get_value("claim_deadline", "")
                if claim_id:
                    await self._recorder.record_claim_submitted(claim_id, claim_amount, deadline)

        # ================================================================
        # PHASE 5: RESOLUTION
        # ================================================================
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=RESOLUTION)
        yield self.emitter.flow_state_changed(flow_name, old_state, RESOLUTION)

        # Build resolution summary
        summary_parts = [f"Incident {incident_id} Resolution Summary:"]

        if "late_checkout" in self._sub_results:
            lc = self._sub_results["late_checkout"]
            approved = lc.get("approved")
            if approved is True:
                checkout_time = self.slot_manager.get_value("john_checkout_time", "N/A")
                fee = self.slot_manager.get_value("late_checkout_fee", 0)
                summary_parts.append(
                    f"  - Late checkout: Approved ({checkout_time}, fee: ${fee})"
                )
            elif approved is False:
                summary_parts.append("  - Late checkout: Denied")
            else:
                summary_parts.append("  - Late checkout: Pending")

        if "cleaner_coordination" in self._sub_results:
            cc = self._sub_results["cleaner_coordination"]
            cleaner = cc.get("cleaner", "N/A")
            summary_parts.append(f"  - Cleaner: {cleaner} dispatched")

        if "photo_inspection" in self._sub_results:
            pi = self._sub_results["photo_inspection"]
            if pi.get("damage_detected"):
                severity = self.slot_manager.get_value("damage_severity", "N/A")
                summary_parts.append(f"  - Damage: Detected (severity: {severity}/5)")
            else:
                summary_parts.append("  - Damage: None detected")

        if "damage_claim" in self._sub_results:
            dc = self._sub_results["damage_claim"]
            if dc.get("skipped"):
                summary_parts.append(f"  - Claim: Skipped ({dc.get('reason', 'N/A')})")
            elif dc.get("claim_id"):
                summary_parts.append(
                    f"  - Claim: {dc['claim_id']} (${dc.get('claim_amount', 0):.2f})"
                )

        resolution_summary = "\n".join(summary_parts)
        self.slot_manager.set_slot("resolution_summary", resolution_summary)
        yield self.emitter.slot_filled("resolution_summary", resolution_summary)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(resolution_summary)
        yield self.emitter.text_message_end()

        # Emit full state snapshot
        yield self.emitter.state_snapshot(
            {
                "incident_id": incident_id,
                "status": "resolved",
                "sub_flows": self._sub_results,
                "slots": self.slot_manager.to_dict(),
            }
        )

        # ================================================================
        # DONE
        # ================================================================
        if self._episodic:
            await self._episodic.add_episode(
                event="incident_resolved",
                content=resolution_summary,
                metadata={"incident_id": incident_id, "sub_flows": list(self._sub_results.keys())},
            )

        self.slot_manager.set_slot("incident_status", "resolved")
        self.slot_manager.set_slot("incident_resolved_at", datetime.utcnow().isoformat())
        yield self.emitter.slot_filled("incident_status", "resolved")
        yield self.emitter.slot_filled("incident_resolved_at", datetime.utcnow().isoformat())

        # ── Memory: Record incident resolution ──────────────────────────
        if self._recorder:
            await self._recorder.record_incident_resolved(summary=resolution_summary)

        if self._cognitive:
            await self._cognitive.process(
                event="incident_resolved",
                content=resolution_summary,
                metadata={"incident_id": incident_id},
            )

        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DONE)
        yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Incident {incident_id} has been resolved. "
            "All phases complete. The property is ready for the next guest."
        )
        yield self.emitter.text_message_end()

        yield self.emitter.flow_completed(
            flow_name,
            {
                "incident_id": incident_id,
                "status": "resolved",
                "sub_flows": self._sub_results,
            },
        )

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal

    @property
    def sub_results(self) -> dict[str, dict[str, Any]]:
        """Results from each completed sub-flow."""
        return dict(self._sub_results)
