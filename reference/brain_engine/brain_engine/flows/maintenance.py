"""Maintenance Incident Flow — Handles property maintenance issues.

Manages incidents like:
- AC not working / кондиционер сломался
- Water leak / протечка
- Broken lock / замок не работает
- Electrical issues / проблемы с электрикой
- Hot water not working / нет горячей воды
- WiFi down / интернет не работает
- Appliance broken / техника сломалась

Escalation chain:
1. Diagnose the issue (guest report + smart home data)
2. Try remote fix (smart AC reset, router reboot, lock reauthorize)
3. If remote fix fails → find vendor from vendor list
4. If no vendor → call manager → call owner
5. If urgent (no heat in winter, water leak) → emergency dispatch
6. Notify guest with ETA and interim solution
7. Track vendor arrival and resolution

States: REPORTED → DIAGNOSE → REMOTE_FIX → FIND_VENDOR → DISPATCH_VENDOR →
       NOTIFY_GUEST → TRACK_REPAIR → VERIFY → DONE
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType, ApprovalStatus
from brain_engine.fallback.fallback_chain import FallbackChain
from brain_engine.problem_solver import ProblemSolver, ProblemAnalysis

logger = logging.getLogger(__name__)

# State constants
REPORTED = "REPORTED"
DIAGNOSE = "DIAGNOSE"
REMOTE_FIX = "REMOTE_FIX"
FIND_VENDOR = "FIND_VENDOR"
DISPATCH_VENDOR = "DISPATCH_VENDOR"
NOTIFY_GUEST = "NOTIFY_GUEST"
TRACK_REPAIR = "TRACK_REPAIR"
VERIFY = "VERIFY"
DONE = "DONE"

STATES = [
    REPORTED, DIAGNOSE, REMOTE_FIX, FIND_VENDOR,
    DISPATCH_VENDOR, NOTIFY_GUEST, TRACK_REPAIR, VERIFY, DONE,
]

# Issue categories with urgency and remote-fixable flag
ISSUE_CATEGORIES: dict[str, dict[str, Any]] = {
    "ac_not_working": {
        "label": "AC / Heating not working",
        "urgency": 3,
        "remote_fixable": True,
        "remote_action": "reset_climate_control",
        "vendor_type": "hvac",
        "interim_solution": "We recommend opening windows for ventilation. A technician is on the way.",
    },
    "water_leak": {
        "label": "Water leak",
        "urgency": 5,
        "remote_fixable": False,
        "vendor_type": "plumber",
        "interim_solution": "Please turn off the main water valve (usually under the kitchen sink). A plumber is being dispatched urgently.",
    },
    "broken_lock": {
        "label": "Lock / Door not working",
        "urgency": 4,
        "remote_fixable": True,
        "remote_action": "regenerate_access_code",
        "vendor_type": "locksmith",
        "interim_solution": "We're generating a new access code. If that doesn't work, a locksmith will arrive shortly.",
    },
    "electrical": {
        "label": "Electrical issue",
        "urgency": 4,
        "remote_fixable": False,
        "vendor_type": "electrician",
        "interim_solution": "Please avoid using the affected area. An electrician is being dispatched.",
    },
    "no_hot_water": {
        "label": "No hot water",
        "urgency": 3,
        "remote_fixable": True,
        "remote_action": "reset_boiler",
        "vendor_type": "plumber",
        "interim_solution": "We're trying to reset the boiler remotely. If that doesn't help, a plumber will come.",
    },
    "wifi_down": {
        "label": "WiFi / Internet not working",
        "urgency": 2,
        "remote_fixable": True,
        "remote_action": "reboot_router",
        "interim_solution": "We're rebooting the router remotely. It should come back within 2-3 minutes.",
    },
    "appliance_broken": {
        "label": "Appliance broken",
        "urgency": 2,
        "remote_fixable": False,
        "vendor_type": "general_repair",
        "interim_solution": "We've noted the issue. A repair technician will be arranged.",
    },
    "other": {
        "label": "Other maintenance issue",
        "urgency": 2,
        "remote_fixable": False,
        "vendor_type": "general_repair",
        "interim_solution": "We're looking into this and will update you shortly.",
    },
}


class MaintenanceFlow:
    """Handles property maintenance incidents from report to resolution.

    Coordinates diagnosis, remote fix attempts, vendor dispatch,
    guest communication, and repair tracking.

    Args:
        slot_manager: SlotManager with property/guest slots.
        emitter: AG-UI event emitter.
        session_id: Unique session identifier.
        voice_client: For calling vendors.
        approval_gateway: For owner approval before dispatch.
        event_recorder: For persistent event logging.
        episodic: For episodic memory recording.
        owner_id: Property owner ID.
        property_id: Property ID.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        voice_client: Any | None = None,
        approval_gateway: ApprovalGateway | None = None,
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
        owner_id: str = "",
        property_id: str = "",
        problem_solver: ProblemSolver | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id
        self._voice = voice_client
        self._approval = approval_gateway
        self._recorder = event_recorder
        self._episodic = episodic
        self._owner_id = owner_id
        self._property_id = property_id
        self._solver = problem_solver

        transitions = [
            Transition(from_state=REPORTED, to_state=DIAGNOSE),
            Transition(from_state=DIAGNOSE, to_state=REMOTE_FIX),
            Transition(from_state=DIAGNOSE, to_state=FIND_VENDOR),
            Transition(from_state=REMOTE_FIX, to_state=NOTIFY_GUEST),  # Fixed remotely
            Transition(from_state=REMOTE_FIX, to_state=FIND_VENDOR),   # Remote fix failed
            Transition(from_state=FIND_VENDOR, to_state=DISPATCH_VENDOR),
            Transition(from_state=FIND_VENDOR, to_state=NOTIFY_GUEST),  # No vendor → notify guest
            Transition(from_state=DISPATCH_VENDOR, to_state=NOTIFY_GUEST),
            Transition(from_state=NOTIFY_GUEST, to_state=TRACK_REPAIR),
            Transition(from_state=NOTIFY_GUEST, to_state=DONE),  # No repair needed
            Transition(from_state=TRACK_REPAIR, to_state=VERIFY),
            Transition(from_state=VERIFY, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=REPORTED,
        )

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the maintenance incident flow.

        Uses ProblemSolver (LLM) to analyze ANY issue and produce
        an action plan. Falls back to keyword-based analysis if LLM unavailable.
        """
        flow_name = "maintenance"
        issue_desc = self.slot_manager.get_value("issue_description", "")
        issue_type = self.slot_manager.get_value("issue_type", "")
        guest_name = self.slot_manager.get_value("guest_name", "Guest")
        property_name = self.slot_manager.get_value("property_name", "Property")
        next_checkin = self.slot_manager.get_value("next_guest_checkin_time", "")

        full_description = f"{issue_type}: {issue_desc}" if issue_type else issue_desc

        yield self.emitter.flow_started(flow_name, REPORTED)

        # ── DIAGNOSE — LLM analyzes the problem ─────────────────────────
        old = self.state_machine.current_state
        self.state_machine.transition(to_state=DIAGNOSE)
        yield self.emitter.flow_state_changed(flow_name, old, DIAGNOSE)

        yield self.emitter.tool_call_start("ai_problem_analysis")
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Analyzing problem: {full_description}..."
        )
        yield self.emitter.text_message_end()

        # Use ProblemSolver or fall back to hardcoded categories
        if self._solver:
            analysis = await self._solver.analyze(
                problem_description=full_description,
                property_context=f"Property: {property_name}",
                reported_by="guest" if guest_name else "system",
                next_checkin=next_checkin,
            )
        else:
            # Fallback: use ISSUE_CATEGORIES if no LLM
            category = ISSUE_CATEGORIES.get(issue_type, ISSUE_CATEGORIES.get("other", {}))
            analysis = ProblemAnalysis(
                problem_summary=category.get("label", full_description[:100]),
                urgency=category.get("urgency", 3),
                category=issue_type or "other",
                can_fix_remotely=category.get("remote_fixable", False),
                remote_fix_steps=[category["remote_action"]] if category.get("remote_action") else [],
                vendor_type_needed=category.get("vendor_type", "general_repair"),
                guest_message=category.get("interim_solution", "We're working on it."),
                interim_solution=category.get("interim_solution", ""),
            )

        yield self.emitter.tool_call_end("ai_problem_analysis", analysis.to_dict())

        # Store analysis results
        self.slot_manager.set_slot("issue_urgency", analysis.urgency)
        self.slot_manager.set_slot("issue_category", analysis.category)
        self.slot_manager.set_slot("is_safety_hazard", analysis.is_safety_hazard)
        yield self.emitter.slot_filled("issue_urgency", analysis.urgency)
        yield self.emitter.slot_filled("issue_category", analysis.category)

        yield self.emitter.reasoning_step(
            "REASONING",
            f"Analysis: {analysis.problem_summary}. "
            f"Urgency: {analysis.urgency}/5. Category: {analysis.category}. "
            f"Remote fixable: {analysis.can_fix_remotely}. "
            f"Vendor needed: {analysis.vendor_type_needed}. "
            f"Affects next checkin: {analysis.affects_checkin}.",
            confidence=0.9,
        )

        # Show action plan
        if analysis.action_plan:
            plan_text = "\n".join(
                f"  {step['step']}. [{step.get('priority', '')}] {step['action']} → {step.get('who', '')}"
                for step in analysis.action_plan
            )
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Action plan:\n{plan_text}")
            yield self.emitter.text_message_end()

        if self._episodic:
            await self._episodic.add_episode(
                event="maintenance_analyzed",
                content=f"{analysis.problem_summary} (urgency {analysis.urgency}/5)",
                metadata=analysis.to_dict(),
            )

        # ── REMOTE FIX (if applicable) ───────────────────────────────────
        remote_fixed = False
        if analysis.can_fix_remotely and analysis.remote_fix_steps:
            old = self.state_machine.current_state
            self.state_machine.transition(to_state=REMOTE_FIX)
            yield self.emitter.flow_state_changed(flow_name, old, REMOTE_FIX)

            for step in analysis.remote_fix_steps:
                yield self.emitter.tool_call_start(f"remote_fix")
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(f"Trying remote fix: {step}...")
                yield self.emitter.text_message_end()
                # In production: call actual API (Seam, Sensibo, router, etc.)
                yield self.emitter.tool_call_end("remote_fix", {"step": step, "success": False})

            # For now, remote fix = false unless explicitly set
            remote_fixed = self.slot_manager.get_value("remote_fix_success", False)

            if remote_fixed:
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content("Remote fix successful!")
                yield self.emitter.text_message_end()

        # ── FIND VENDOR ──────────────────────────────────────────────────
        if not remote_fixed and analysis.vendor_type_needed != "none":
            old = self.state_machine.current_state
            self.state_machine.transition(to_state=FIND_VENDOR)
            yield self.emitter.flow_state_changed(flow_name, old, FIND_VENDOR)

            vendor = self._find_vendor(analysis.vendor_type_needed)

            if vendor:
                if self._approval:
                    yield self.emitter.tool_call_start("request_owner_approval")
                    approval_result = await self._approval.request_approval(
                        action_type=ActionType.CALL_VENDOR,
                        owner_id=self._owner_id,
                        property_id=self._property_id,
                        description=(
                            f"Dispatch {analysis.vendor_type_needed}: '{vendor['name']}'. "
                            f"Issue: {analysis.problem_summary}. "
                            f"Urgency: {analysis.urgency}/5."
                        ),
                        proposed_action={"vendor": vendor, "analysis": analysis.to_dict()},
                        urgency=analysis.urgency,
                    )
                    yield self.emitter.tool_call_end(
                        "request_owner_approval",
                        {"status": approval_result.status.value},
                    )
                    if approval_result.status == ApprovalStatus.DENIED:
                        vendor = None

                if vendor:
                    old = self.state_machine.current_state
                    self.state_machine.transition(to_state=DISPATCH_VENDOR)
                    yield self.emitter.flow_state_changed(flow_name, old, DISPATCH_VENDOR)

                    self.slot_manager.set_slot("vendor_name", vendor["name"])
                    self.slot_manager.set_slot("vendor_phone", vendor["phone"])
                    yield self.emitter.slot_filled("vendor_name", vendor["name"])

                    yield self.emitter.text_message_start()
                    yield self.emitter.text_message_content(
                        f"Vendor {vendor['name']} dispatched. Contact: {vendor['phone']}."
                    )
                    yield self.emitter.text_message_end()
            else:
                # Owner notification from analysis
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"No {analysis.vendor_type_needed} vendor available.\n"
                    f"Owner notified: {analysis.owner_message}"
                )
                yield self.emitter.text_message_end()

        # ── NOTIFY GUEST ─────────────────────────────────────────────────
        old = self.state_machine.current_state
        self.state_machine.transition(to_state=NOTIFY_GUEST)
        yield self.emitter.flow_state_changed(flow_name, old, NOTIFY_GUEST)

        # Use LLM-generated guest message
        if remote_fixed:
            guest_msg = f"The issue has been resolved remotely: {analysis.problem_summary}."
        elif analysis.guest_message:
            guest_msg = analysis.guest_message
        else:
            guest_msg = f"We're aware of the issue and working on it. {analysis.interim_solution}"

        self.slot_manager.set_slot("guest_notification", guest_msg)
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(f"Guest notification: {guest_msg}")
        yield self.emitter.text_message_end()

        # ── TRACK / VERIFY / DONE ────────────────────────────────────────
        if remote_fixed or not self.slot_manager.get_value("vendor_name"):
            old = self.state_machine.current_state
            self.state_machine.transition(to_state=DONE)
            yield self.emitter.flow_state_changed(flow_name, old, DONE)
        else:
            for next_state in (TRACK_REPAIR, VERIFY, DONE):
                old = self.state_machine.current_state
                self.state_machine.transition(to_state=next_state)
                yield self.emitter.flow_state_changed(flow_name, old, next_state)

        if self._recorder:
            await self._recorder.record_incident_update(
                event="maintenance_resolved",
                details=f"Issue: {analysis.problem_summary}. Vendor: {self.slot_manager.get_value('vendor_name', 'none')}.",
                issue_type=analysis.category,
                remote_fixed=remote_fixed,
                urgency=analysis.urgency,
            )

        yield self.emitter.flow_completed(flow_name, {
            "analysis": analysis.to_dict(),
            "remote_fixed": remote_fixed,
            "vendor_dispatched": bool(self.slot_manager.get_value("vendor_name")),
        })

    def _find_vendor(self, vendor_type: str) -> dict[str, Any] | None:
        """Find a vendor by type from config/vendors.json."""
        import json
        from pathlib import Path

        vendors_path = Path(__file__).resolve().parents[2] / "config" / "vendors.json"
        try:
            with open(vendors_path) as f:
                vendors = json.load(f)
            if isinstance(vendors, list):
                for v in vendors:
                    v_type = v.get("type", v.get("specialty", "")).lower()
                    if vendor_type.lower() in v_type or v_type in vendor_type.lower():
                        return v
                # Fallback: return first available vendor
                if vendors:
                    return vendors[0]
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("vendors.json not found or invalid")
        return None

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal
