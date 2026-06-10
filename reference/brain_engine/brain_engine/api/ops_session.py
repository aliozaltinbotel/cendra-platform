"""Ops Session Manager — Multi-turn session tracking for operations.

Tracks the full lifecycle of an ops event from creation to resolution.
Handles the 10 edge cases from Cendra ops case study:

    1. Simple success (cleaner confirms)
    2. Decline with alternative contact
    3. Cost negotiation + PM approval
    4. No reply -> follow-up -> escalation
    5. Ambiguous reply (needs clarification)
    6. Multi-issue (parallel dispatch)
    7. No contacts / wrong category
    8. Voice note / image from vendor
    9. Late confirmation after timeout
   10. Recurring issue (same problem returns)

Each session tracks:
    - Contact cascade state (who was tried, who declined, who's active)
    - Conversation history per contact
    - Cost quotes and PM approval requests
    - Resolution status and timeline
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "brain:ops:"
_SESSION_TTL = 7 * 86400  # 7 days


class SessionStatus(str, Enum):
    """Possible states for an ops session."""

    CREATED = "created"
    DISPATCHING = "dispatching"           # contacting first contact
    WAITING_REPLY = "waiting_reply"       # waiting for contact response
    FOLLOW_UP_SENT = "follow_up_sent"     # follow-up sent, waiting
    NEGOTIATING = "negotiating"           # cost/terms negotiation
    PENDING_APPROVAL = "pending_approval" # waiting for PM/owner approval
    APPROVED = "approved"                 # PM approved, proceeding
    REJECTED = "rejected"                 # PM rejected
    RESOLVED = "resolved"                 # issue resolved
    ESCALATED = "escalated"              # escalated to PM (no contacts/all declined)
    CANCELLED = "cancelled"              # manually cancelled


class ContactStatus(str, Enum):
    """Status of a contact attempt."""

    PENDING = "pending"
    CONTACTED = "contacted"
    WAITING = "waiting"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    NO_REPLY = "no_reply"
    ALTERNATIVE_OFFERED = "alternative_offered"
    COST_QUOTED = "cost_quoted"
    AMBIGUOUS = "ambiguous"


@dataclass
class ContactAttempt:
    """Record of attempting to contact a cleaner/vendor.

    Attributes:
        contact_id: Contact identifier.
        contact_name: Contact display name.
        contact_type: 'cleaner' or 'vendor'.
        status: Current contact status.
        contacted_at: When first contacted.
        last_message_at: When last message was sent/received.
        messages: Conversation history with this contact.
        cost_quoted: Cost quoted by the contact (if any).
        cost_currency: Currency of the quote.
        decline_reason: Why they declined (if applicable).
        alternative_contact: Alternative suggested (Scenario 2).
        follow_up_count: Number of follow-ups sent.
        eta_minutes: Estimated arrival time if confirmed.
    """

    contact_id: str = ""
    contact_name: str = ""
    contact_type: str = ""
    status: str = ContactStatus.PENDING
    contacted_at: str = ""
    last_message_at: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    cost_quoted: float | None = None
    cost_currency: str = ""
    decline_reason: str = ""
    alternative_contact: dict[str, str] | None = None
    follow_up_count: int = 0
    eta_minutes: int | None = None


@dataclass
class OpsSession:
    """Full state of an operational session.

    Attributes:
        session_id: Unique session identifier.
        property_id: Property where the issue is.
        reservation_id: Associated reservation (if any).
        event_type: Type of ops event.
        category: Issue category (Cleaning, Plumbing, etc.).
        subcategory: Issue subcategory.
        description: Description of the issue.
        status: Current session status.
        created_at: When the session was created.
        updated_at: Last update timestamp.
        resolved_at: When resolved (if applicable).
        contact_cascade: Ordered list of contacts to try.
        current_contact_index: Which contact we're currently on.
        contact_attempts: History of all contact attempts.
        cost_threshold: Max cost before PM approval needed.
        pm_approval_request: Pending PM approval details.
        guest_update_sent: Whether guest has been updated.
        guest_update_text: Last update sent to guest.
        is_recurring: Whether this is a repeat of a previous issue.
        previous_session_id: ID of the previous session (if recurring).
        parallel_sessions: IDs of parallel sessions (multi-issue).
        resolution_summary: How the issue was resolved.
        tags: Classification tags.
    """

    session_id: str = ""
    property_id: str = ""
    reservation_id: str = ""
    event_type: str = ""
    category: str = ""
    subcategory: str = ""
    description: str = ""
    status: str = SessionStatus.CREATED
    created_at: str = ""
    updated_at: str = ""
    resolved_at: str = ""
    contact_cascade: list[str] = field(default_factory=list)
    current_contact_index: int = 0
    contact_attempts: list[dict[str, Any]] = field(default_factory=list)
    cost_threshold: float = 200.0
    pm_approval_request: dict[str, Any] | None = None
    guest_update_sent: bool = False
    guest_update_text: str = ""
    is_recurring: bool = False
    previous_session_id: str = ""
    parallel_sessions: list[str] = field(default_factory=list)
    resolution_summary: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Auto-generate ID and timestamps."""
        if not self.session_id:
            self.session_id = f"ops-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    @property
    def current_contact(self) -> str | None:
        """Get the current contact being tried."""
        if self.current_contact_index < len(self.contact_cascade):
            return self.contact_cascade[self.current_contact_index]
        return None

    @property
    def all_contacts_exhausted(self) -> bool:
        """Check if all contacts in the cascade have been tried."""
        return self.current_contact_index >= len(self.contact_cascade)

    @property
    def has_pending_approval(self) -> bool:
        """Check if there's a pending PM approval."""
        return self.status == SessionStatus.PENDING_APPROVAL


class OpsSessionManager:
    """Manages multi-turn ops sessions with contact cascade tracking.

    Handles the full lifecycle from event creation through contact
    cascade, negotiation, approval, and resolution.

    Args:
        redis_client: Async Redis client for session persistence.
    """

    def __init__(
        self,
        redis_client: Any,
    ) -> None:
        self._redis = redis_client

    # ── Session Lifecycle ───────────────────────────────────────────── #

    async def create_session(
        self,
        property_id: str,
        event_type: str,
        category: str,
        description: str,
        contact_cascade: list[str],
        cost_threshold: float = 200.0,
        reservation_id: str = "",
        subcategory: str = "",
        tags: list[str] | None = None,
    ) -> OpsSession:
        """Create a new ops session.

        Checks for recurring issues (Scenario 10) and duplicate
        sessions before creation.

        Args:
            property_id: Property identifier.
            event_type: Event type.
            category: Issue category.
            description: Issue description.
            contact_cascade: Ordered list of contact IDs to try.
            cost_threshold: Max cost before PM approval.
            reservation_id: Optional reservation ID.
            subcategory: Optional subcategory.
            tags: Optional classification tags.

        Returns:
            The created OpsSession.
        """
        # Check for duplicate active session (same property + category)
        existing = await self._find_active_session(property_id, category)
        if existing:
            logger.warning(
                "Duplicate session blocked: property=%s category=%s existing=%s",
                property_id, category, existing.session_id,
            )
            return existing

        # Check for recurring issue (Scenario 10)
        previous = await self._find_recent_resolved(property_id, category)

        session = OpsSession(
            property_id=property_id,
            reservation_id=reservation_id,
            event_type=event_type,
            category=category,
            subcategory=subcategory,
            description=description,
            contact_cascade=contact_cascade,
            cost_threshold=cost_threshold,
            tags=tags or [],
        )

        if previous:
            session.is_recurring = True
            session.previous_session_id = previous.session_id
            logger.info(
                "Recurring issue detected: previous=%s",
                previous.session_id,
            )

        await self._save(session)
        return session

    async def get_session(self, session_id: str) -> OpsSession | None:
        """Fetch a session by ID.

        Args:
            session_id: Session identifier.

        Returns:
            OpsSession or None.
        """
        return await self._load(session_id)

    # ── Contact Cascade ─────────────────────────────────────────────── #

    async def dispatch_next_contact(
        self, session_id: str,
    ) -> dict[str, Any]:
        """Move to the next contact in the cascade and dispatch.

        Handles:
        - Scenario 4: No reply -> try next
        - Scenario 7: No contacts left -> escalate

        Args:
            session_id: Session identifier.

        Returns:
            Dict with action to take (contact info or escalation).
        """
        session = await self._load(session_id)
        if not session:
            return {"error": "session_not_found"}

        if session.all_contacts_exhausted:
            session.status = SessionStatus.ESCALATED
            await self._save(session)
            return {
                "action": "escalate_to_pm",
                "reason": "all_contacts_exhausted",
                "session_id": session_id,
            }

        contact_id = session.current_contact
        session.status = SessionStatus.DISPATCHING
        session.updated_at = datetime.now(timezone.utc).isoformat()

        attempt = ContactAttempt(
            contact_id=contact_id or "",
            status=ContactStatus.CONTACTED,
            contacted_at=datetime.now(timezone.utc).isoformat(),
        )
        session.contact_attempts.append(asdict(attempt))
        await self._save(session)

        return {
            "action": "contact",
            "contact_id": contact_id,
            "session_id": session_id,
            "is_recurring": session.is_recurring,
            "previous_contact": self._get_previous_contact(session),
        }

    def _get_previous_contact(self, session: OpsSession) -> str:
        """For recurring issues, get who handled it last time.

        Args:
            session: Current session.

        Returns:
            Previous contact ID or empty string.
        """
        if not session.is_recurring or not session.contact_attempts:
            return ""
        # In recurring issues, we might want a DIFFERENT contact
        return ""

    # ── Reply Processing ────────────────────────────────────────────── #

    async def process_reply(
        self,
        session_id: str,
        contact_id: str,
        message: str,
        reply_classification: str,
        cost_amount: float | None = None,
        cost_currency: str = "",
        alternative_contact: dict[str, str] | None = None,
        eta_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Process a contact's reply and determine next action.

        Handles all reply scenarios:
        - Scenario 1: Confirmed -> resolve
        - Scenario 2: Declined + alternative -> create new contact, dispatch
        - Scenario 3: Cost quote -> check threshold -> approve/escalate
        - Scenario 5: Ambiguous -> ask for clarification
        - Scenario 8: Unprocessable (voice/image) -> ask for text
        - Scenario 9: Late reply -> check if still needed

        Args:
            session_id: Session identifier.
            contact_id: Replying contact's ID.
            message: Reply message text.
            reply_classification: Classified reply type.
            cost_amount: Cost quoted (if any).
            cost_currency: Currency of the quote.
            alternative_contact: Alternative contact info (Scenario 2).
            eta_minutes: ETA if confirmed.

        Returns:
            Dict with the next action to take.
        """
        session = await self._load(session_id)
        if not session:
            return {"error": "session_not_found"}

        # Record the message in the contact attempt
        self._record_reply(session, contact_id, message)

        handler = _REPLY_HANDLERS.get(
            reply_classification, self._handle_unknown_reply,
        )
        result = await handler(
            self, session, contact_id, message,
            cost_amount, cost_currency, alternative_contact, eta_minutes,
        )

        session.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save(session)
        return result

    def _record_reply(
        self,
        session: OpsSession,
        contact_id: str,
        message: str,
    ) -> None:
        """Record a reply message in the contact attempt history.

        Args:
            session: The ops session.
            contact_id: Contact who replied.
            message: Reply message text.
        """
        now = datetime.now(timezone.utc).isoformat()
        for attempt in session.contact_attempts:
            if attempt.get("contact_id") == contact_id:
                attempt.setdefault("messages", []).append({
                    "role": "contact",
                    "content": message,
                    "timestamp": now,
                })
                attempt["last_message_at"] = now
                return

    # ── Reply Handlers ──────────────────────────────────────────────── #

    async def _handle_confirmed(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle confirmed reply (Scenario 1).

        Args:
            session: The ops session.
            contact_id: Contact who confirmed.
            message: Reply text.
            cost: Cost if mentioned.
            currency: Cost currency.
            alt: Not used for confirmation.
            eta: ETA in minutes.

        Returns:
            Resolution action dict.
        """
        self._update_attempt_status(session, contact_id, ContactStatus.CONFIRMED)

        session.status = SessionStatus.RESOLVED
        session.resolved_at = datetime.now(timezone.utc).isoformat()
        session.resolution_summary = (
            f"Contact {contact_id} confirmed. "
            f"ETA: {eta} minutes." if eta else f"Contact {contact_id} confirmed."
        )

        return {
            "action": "notify_guest",
            "contact_id": contact_id,
            "eta_minutes": eta,
            "message_to_guest": self._build_guest_update(session, eta),
            "session_resolved": True,
        }

    async def _handle_declined(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle declined reply (Scenario 2).

        Args:
            session: The ops session.
            contact_id: Contact who declined.
            message: Reply text.
            cost: Not used.
            currency: Not used.
            alt: Alternative contact info if provided.
            eta: Not used.

        Returns:
            Next action (try alternative or next in cascade).
        """
        self._update_attempt_status(session, contact_id, ContactStatus.DECLINED)
        session.current_contact_index += 1

        if alt:
            return {
                "action": "create_and_contact_alternative",
                "alternative_contact": alt,
                "session_id": session.session_id,
                "mcp_tools": [
                    {"tool": "createContact", "params": alt},
                    {"tool": "sendWhatsApp", "params": {
                        "phone": alt.get("phone", ""),
                        "message": self._build_dispatch_message(session),
                    }},
                ],
            }

        return await self.dispatch_next_contact(session.session_id)

    async def _handle_cost_quoted(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle cost quote reply (Scenario 3).

        Args:
            session: The ops session.
            contact_id: Contact who quoted.
            message: Reply text.
            cost: Quoted cost amount.
            currency: Cost currency.
            alt: Not used.
            eta: Not used.

        Returns:
            Auto-approve or escalate to PM.
        """
        self._update_attempt_status(session, contact_id, ContactStatus.COST_QUOTED)

        # Update attempt with cost info
        for attempt in session.contact_attempts:
            if attempt.get("contact_id") == contact_id:
                attempt["cost_quoted"] = cost
                attempt["cost_currency"] = currency

        if cost is not None and cost <= session.cost_threshold:
            # Auto-approve: cost within threshold
            session.status = SessionStatus.APPROVED
            return {
                "action": "approve_and_confirm",
                "contact_id": contact_id,
                "cost": cost,
                "currency": currency,
                "auto_approved": True,
                "mcp_tools": [
                    {"tool": "sendWhatsApp", "params": {
                        "contact_id": contact_id,
                        "message": f"Approved. Please proceed. Budget: {cost} {currency}",
                    }},
                ],
            }

        # Cost exceeds threshold -> PM approval required
        session.status = SessionStatus.PENDING_APPROVAL
        session.pm_approval_request = {
            "contact_id": contact_id,
            "cost": cost,
            "currency": currency,
            "threshold": session.cost_threshold,
            "description": session.description,
        }

        return {
            "action": "request_pm_approval",
            "cost": cost,
            "currency": currency,
            "threshold": session.cost_threshold,
            "session_id": session.session_id,
            "mcp_tools": [
                {"tool": "createTask", "params": {
                    "task": f"Approve cost: {cost} {currency}",
                    "description": (
                        f"Contact quoted {cost} {currency} for "
                        f"{session.category}. Threshold: {session.cost_threshold} "
                        f"{currency}. Approve or reject."
                    ),
                    "main_category": session.category,
                }},
            ],
        }

    async def _handle_ambiguous(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle ambiguous reply (Scenario 5).

        Args:
            session: The ops session.
            contact_id: Contact who gave ambiguous reply.
            message: Reply text.
            cost: Not used.
            currency: Not used.
            alt: Not used.
            eta: Not used.

        Returns:
            Clarification request action.
        """
        self._update_attempt_status(session, contact_id, ContactStatus.AMBIGUOUS)
        session.status = SessionStatus.NEGOTIATING

        return {
            "action": "request_clarification",
            "contact_id": contact_id,
            "session_id": session.session_id,
            "mcp_tools": [
                {"tool": "sendWhatsApp", "params": {
                    "contact_id": contact_id,
                    "message": (
                        "Thank you for your response. Could you please confirm: "
                        "are you able to come? If yes, approximately when?"
                    ),
                }},
            ],
        }

    async def _handle_no_reply(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle no reply / timeout (Scenario 4).

        Args:
            session: The ops session.
            contact_id: Contact who didn't reply.
            message: Not used.
            cost: Not used.
            currency: Not used.
            alt: Not used.
            eta: Not used.

        Returns:
            Follow-up or move to next contact.
        """
        attempt = self._get_attempt(session, contact_id)
        follow_up_count = attempt.get("follow_up_count", 0) if attempt else 0

        if follow_up_count == 0:
            # First timeout: send follow-up
            self._update_attempt_status(session, contact_id, ContactStatus.WAITING)
            if attempt:
                attempt["follow_up_count"] = 1
            session.status = SessionStatus.FOLLOW_UP_SENT

            return {
                "action": "send_follow_up",
                "contact_id": contact_id,
                "session_id": session.session_id,
                "mcp_tools": [
                    {"tool": "sendWhatsApp", "params": {
                        "contact_id": contact_id,
                        "message": (
                            "Hi, just checking in. Are you available "
                            f"for {session.category.lower()} at the property? "
                            "Please let us know."
                        ),
                    }},
                ],
            }

        # Already followed up: move to next contact
        self._update_attempt_status(session, contact_id, ContactStatus.NO_REPLY)
        session.current_contact_index += 1
        return await self.dispatch_next_contact(session.session_id)

    async def _handle_unprocessable(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle unprocessable reply — voice note / image (Scenario 8).

        Args:
            session: The ops session.
            contact_id: Contact who sent unprocessable content.
            message: Description of what was sent.
            cost: Not used.
            currency: Not used.
            alt: Not used.
            eta: Not used.

        Returns:
            Request for text-based reply.
        """
        return {
            "action": "request_text_reply",
            "contact_id": contact_id,
            "session_id": session.session_id,
            "mcp_tools": [
                {"tool": "sendWhatsApp", "params": {
                    "contact_id": contact_id,
                    "message": (
                        "Thank you! Unfortunately I can only process text messages "
                        "at the moment. Could you please reply in text? "
                        "Specifically: can you come, and approximately when?"
                    ),
                }},
            ],
        }

    async def _handle_unknown_reply(
        self, session: OpsSession, contact_id: str,
        message: str, cost: float | None, currency: str,
        alt: dict[str, str] | None, eta: int | None,
    ) -> dict[str, Any]:
        """Handle unclassified reply — treat as ambiguous.

        Args:
            session: The ops session.
            contact_id: Contact.
            message: Reply text.
            cost: Not used.
            currency: Not used.
            alt: Not used.
            eta: Not used.

        Returns:
            Clarification request.
        """
        return await self._handle_ambiguous(
            session, contact_id, message, cost, currency, alt, eta,
        )

    # ── PM Approval ─────────────────────────────────────────────────── #

    async def process_pm_decision(
        self,
        session_id: str,
        approved: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        """Process PM's approval/rejection of a cost quote.

        Args:
            session_id: Session identifier.
            approved: Whether PM approved.
            reason: Reason for decision.

        Returns:
            Next action dict.
        """
        session = await self._load(session_id)
        if not session:
            return {"error": "session_not_found"}

        if approved:
            session.status = SessionStatus.APPROVED
            contact_id = (
                session.pm_approval_request or {}
            ).get("contact_id", "")
            return {
                "action": "confirm_with_contact",
                "contact_id": contact_id,
                "session_id": session_id,
                "mcp_tools": [
                    {"tool": "sendWhatsApp", "params": {
                        "contact_id": contact_id,
                        "message": "Approved. Please proceed with the work.",
                    }},
                ],
            }

        # Rejected: try next contact or escalate
        session.status = SessionStatus.REJECTED
        session.current_contact_index += 1
        await self._save(session)
        return await self.dispatch_next_contact(session_id)

    # ── Late Reply Handling (Scenario 9) ────────────────────────────── #

    async def handle_late_reply(
        self,
        session_id: str,
        contact_id: str,
        message: str,
    ) -> dict[str, Any]:
        """Handle a late reply from a contact after timeout.

        If session is already resolved by another contact, acknowledge
        and decline. If still open, process as normal reply.

        Args:
            session_id: Session identifier.
            contact_id: Contact who replied late.
            message: Reply text.

        Returns:
            Action dict.
        """
        session = await self._load(session_id)
        if not session:
            return {"error": "session_not_found"}

        if session.status == SessionStatus.RESOLVED:
            return {
                "action": "decline_late_reply",
                "contact_id": contact_id,
                "session_id": session_id,
                "reason": "already_resolved",
                "mcp_tools": [
                    {"tool": "sendWhatsApp", "params": {
                        "contact_id": contact_id,
                        "message": (
                            "Thank you for getting back to us! "
                            "This issue has already been resolved. "
                            "We'll keep you in mind for future requests."
                        ),
                    }},
                ],
            }

        # Session still open — accept the late reply
        return {
            "action": "process_late_reply",
            "contact_id": contact_id,
            "session_id": session_id,
            "note": "Session still active — processing as normal reply",
        }

    # ── Helpers ─────────────────────────────────────────────────────── #

    def _update_attempt_status(
        self, session: OpsSession, contact_id: str, status: str,
    ) -> None:
        """Update the status of a contact attempt.

        Args:
            session: The ops session.
            contact_id: Contact identifier.
            status: New status.
        """
        for attempt in session.contact_attempts:
            if attempt.get("contact_id") == contact_id:
                attempt["status"] = status
                return

    def _get_attempt(
        self, session: OpsSession, contact_id: str,
    ) -> dict[str, Any] | None:
        """Get a contact attempt by contact_id.

        Args:
            session: The ops session.
            contact_id: Contact identifier.

        Returns:
            The attempt dict or None.
        """
        for attempt in session.contact_attempts:
            if attempt.get("contact_id") == contact_id:
                return attempt
        return None

    @staticmethod
    def _build_guest_update(
        session: OpsSession, eta: int | None,
    ) -> str:
        """Build a guest update message.

        Args:
            session: The ops session.
            eta: ETA in minutes (if known).

        Returns:
            Guest-facing update message.
        """
        if eta:
            return (
                f"We've arranged someone to help with the "
                f"{session.category.lower()} issue. "
                f"They'll be there in approximately {eta} minutes."
            )
        return (
            f"We've arranged someone to help with the "
            f"{session.category.lower()} issue. "
            f"They'll be in touch shortly."
        )

    @staticmethod
    def _build_dispatch_message(session: OpsSession) -> str:
        """Build the initial dispatch message to a contact.

        Args:
            session: The ops session.

        Returns:
            Dispatch message text.
        """
        return (
            f"Hi, we need assistance with a {session.category.lower()} "
            f"issue at the property. {session.description} "
            f"Are you available to help?"
        )

    # ── Persistence ─────────────────────────────────────────────────── #

    async def _save(self, session: OpsSession) -> None:
        """Save session to Redis.

        Args:
            session: The session to save.
        """
        key = f"{_PREFIX}{session.session_id}"
        data = json.dumps(asdict(session), default=str)
        await self._redis.set(key, data, ex=_SESSION_TTL)

        # Add to property index
        await self._redis.sadd(
            f"{_PREFIX}by_prop:{session.property_id}",
            session.session_id,
        )

    async def _load(self, session_id: str) -> OpsSession | None:
        """Load session from Redis.

        Args:
            session_id: Session identifier.

        Returns:
            OpsSession or None.
        """
        raw = await self._redis.get(f"{_PREFIX}{session_id}")
        if not raw:
            return None
        data = json.loads(raw)
        return OpsSession(**{
            k: v for k, v in data.items()
            if k in OpsSession.__dataclass_fields__
        })

    async def _find_active_session(
        self, property_id: str, category: str,
    ) -> OpsSession | None:
        """Find an active session for the same property + category.

        Prevents duplicate dispatch (single session per property+category).

        Args:
            property_id: Property identifier.
            category: Issue category.

        Returns:
            Active OpsSession or None.
        """
        session_ids = await self._redis.smembers(
            f"{_PREFIX}by_prop:{property_id}",
        )
        for sid in session_ids:
            session = await self._load(sid)
            if not session:
                continue
            if session.category != category:
                continue
            if session.status in (
                SessionStatus.RESOLVED, SessionStatus.CANCELLED,
            ):
                continue
            return session
        return None

    async def _find_recent_resolved(
        self, property_id: str, category: str,
    ) -> OpsSession | None:
        """Find a recently resolved session (for recurring detection).

        Args:
            property_id: Property identifier.
            category: Issue category.

        Returns:
            Recently resolved OpsSession or None.
        """
        session_ids = await self._redis.smembers(
            f"{_PREFIX}by_prop:{property_id}",
        )
        for sid in session_ids:
            session = await self._load(sid)
            if not session:
                continue
            if session.category != category:
                continue
            if session.status == SessionStatus.RESOLVED:
                return session
        return None


# ── Reply handler mapping ───────────────────────────────────────────── #

_REPLY_HANDLERS: dict[str, Any] = {
    "confirmed": OpsSessionManager._handle_confirmed,
    "declined": OpsSessionManager._handle_declined,
    "cost_quoted": OpsSessionManager._handle_cost_quoted,
    "ambiguous": OpsSessionManager._handle_ambiguous,
    "no_reply": OpsSessionManager._handle_no_reply,
    "unprocessable": OpsSessionManager._handle_unprocessable,
}
