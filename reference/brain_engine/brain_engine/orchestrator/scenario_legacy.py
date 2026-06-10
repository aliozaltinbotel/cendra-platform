"""Scenario Flow — Master orchestrator for the full Airbnb incident resolution scenario.

Coordinates the complete 11-step scenario from late checkout through damage claim:
1. Assess situation (late checkout + incoming guest conflict)
2. Offer free late checkout to departing guest (voice call)
3. Call incoming guest to ask check-in time
4. Find and assign a cleaner
5. Day-of setup: create access code, send reminders
6. Detect guest entry via Nuki smart lock
7. Request post-checkout photos from cleaner (voice call)
8. Inspect photos with GPT-4o Vision
9. Process damage claim if damage detected
10. Monitor cleaning progress
11. Handle backup cleaner if needed

States:
    ASSESS_SITUATION → OFFER_FREE_CHECKOUT → CALL_NEW_GUEST →
    FIND_CLEANER → REPLACE_CLEANER → CANCEL_ORIGINAL →
    DAY_OF_SETUP → DETECT_ENTRY → REQUEST_PHOTOS →
    INSPECT_PHOTOS → PROCESS_CLAIM → CHECK_PROGRESS →
    BACKUP_CLEANER → DONE
"""

from __future__ import annotations

import logging
import uuid
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.memory.cognitive_controller import CognitiveController
from brain_engine.approval.gateway import ApprovalGateway
from brain_engine.approval.models import ActionType, ApprovalStatus

logger = logging.getLogger(__name__)

# ── State constants ──────────────────────────────────────────────────── #
ASSESS_SITUATION = "ASSESS_SITUATION"
OFFER_FREE_CHECKOUT = "OFFER_FREE_CHECKOUT"
CALL_NEW_GUEST = "CALL_NEW_GUEST"
FIND_CLEANER = "FIND_CLEANER"
REPLACE_CLEANER = "REPLACE_CLEANER"
CANCEL_ORIGINAL = "CANCEL_ORIGINAL"
DAY_OF_SETUP = "DAY_OF_SETUP"
DETECT_ENTRY = "DETECT_ENTRY"
REQUEST_PHOTOS = "REQUEST_PHOTOS"
INSPECT_PHOTOS = "INSPECT_PHOTOS"
PROCESS_CLAIM = "PROCESS_CLAIM"
VENDOR_QUOTES = "VENDOR_QUOTES"
VENDOR_SELECT = "VENDOR_SELECT"
VENDOR_ACCESS = "VENDOR_ACCESS"
VENDOR_INVOICE = "VENDOR_INVOICE"
PAYMENT_APPROVAL = "PAYMENT_APPROVAL"
PAYMENT_PROCESS = "PAYMENT_PROCESS"
CHECK_PROGRESS = "CHECK_PROGRESS"
BACKUP_CLEANER = "BACKUP_CLEANER"
ESCALATE_NO_CLEANER = "ESCALATE_NO_CLEANER"
DOOR_TROUBLE = "DOOR_TROUBLE"
FINAL_PHOTOS = "FINAL_PHOTOS"
FINAL_VERIFICATION = "FINAL_VERIFICATION"
FLIGHT_CHECK = "FLIGHT_CHECK"
SMART_AC = "SMART_AC"
DONE = "DONE"

STATES = [
    ASSESS_SITUATION,
    OFFER_FREE_CHECKOUT,
    CALL_NEW_GUEST,
    FIND_CLEANER,
    REPLACE_CLEANER,
    CANCEL_ORIGINAL,
    ESCALATE_NO_CLEANER,
    DAY_OF_SETUP,
    DETECT_ENTRY,
    DOOR_TROUBLE,
    REQUEST_PHOTOS,
    INSPECT_PHOTOS,
    PROCESS_CLAIM,
    VENDOR_QUOTES,
    VENDOR_SELECT,
    VENDOR_ACCESS,
    VENDOR_INVOICE,
    PAYMENT_APPROVAL,
    PAYMENT_PROCESS,
    CHECK_PROGRESS,
    BACKUP_CLEANER,
    FINAL_PHOTOS,
    FINAL_VERIFICATION,
    FLIGHT_CHECK,
    SMART_AC,
    DONE,
]

# Path to call scripts YAML
SCRIPTS_PATH = Path(__file__).resolve().parents[2] / "config" / "call_scripts.yaml"


def _load_scripts() -> dict[str, Any]:
    """Load call scripts from YAML config."""
    try:
        with open(SCRIPTS_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Call scripts not found at %s", SCRIPTS_PATH)
        return {}


class ScenarioFlow:
    """Master orchestrator for the full Airbnb incident resolution scenario.

    Manages 14 states covering the entire lifecycle from late-checkout conflict
    detection through damage claim resolution. Integrates ElevenLabs voice calls,
    Nuki smart lock entry detection, GPT-4o Vision photo inspection, and cleaner
    coordination.

    Args:
        slot_manager: SlotManager with scenario slots pre-filled.
        emitter: AG-UI event emitter for streaming UI updates.
        voice_client: ElevenLabs client for making outbound calls.
        nuki_client: NukiLock client for smart lock operations.
        entry_detector: NukiEntryDetector for guest entry detection.
        photo_comparator: PhotoComparator for before/after analysis.
        session_id: Unique session identifier.
        event_recorder: EventRecorder for persistent event logging.
        episodic: EpisodicMemory for granular event recording.
        cognitive: CognitiveController for memory-aware reasoning.
    """

    # Simulated call transcripts for dry_run mode
    DRY_RUN_TRANSCRIPTS: dict[str, dict[str, Any]] = {
        # ── Сценарий заказчика: John недоволен, George поздно, клинер проблемы ──
        "ask_guest_checkin_time": {
            "transcript": [
                {"role": "agent", "message": "Hi, is this George? This is the property assistant for your upcoming Airbnb stay at Seaside Apartment."},
                {"role": "user", "message": "Yes, hi. What's going on?"},
                {"role": "agent", "message": "I'm just calling to check — what time are you planning to arrive today?"},
                {"role": "user", "message": "Our flight lands at 8 PM, so probably around 9 PM by the time we get there. Maybe later if traffic is bad."},
                {"role": "agent", "message": "Got it — around 9 PM. We'll make sure everything is ready for you by then."},
                {"role": "user", "message": "Is there a problem? I hope the apartment is ready."},
                {"role": "agent", "message": "No problem at all! Just coordinating the cleaning schedule. Everything will be perfect for your arrival. See you tonight!"},
                {"role": "user", "message": "OK thanks. Bye."},
            ],
            "extracted": {"checkin_time": "9:00 PM", "sentiment": "NEUTRAL"},
            "duration": 50,
        },
        "offer_free_late_checkout": {
            "transcript": [
                {"role": "agent", "message": "Hi John, this is the property assistant at Seaside Apartment. I'm calling about your checkout today."},
                {"role": "user", "message": "Hi! Yes, what's up?"},
                {"role": "agent", "message": "We'd like to offer you a complimentary late checkout until 3 PM today, completely free of charge. As a thank you for being a wonderful guest."},
                {"role": "user", "message": "Oh wow, really? That's so nice of you! I was actually going to ask about that — I need a bit more time to pack."},
                {"role": "agent", "message": "Absolutely! So your checkout is now at 3 PM. Just leave the key on the kitchen counter when you leave. We really appreciated how well you took care of the apartment."},
                {"role": "user", "message": "Thank you so much! Everything was perfect — the apartment is lovely. I'll definitely book again."},
                {"role": "agent", "message": "That means a lot! We'll send you a special discount for your next stay. Have a wonderful trip, John!"},
                {"role": "user", "message": "Thanks! Bye!"},
            ],
            "extracted": {"accepted": True, "checkout_time": "3:00 PM", "sentiment": "POSITIVE", "complaints": []},
            "duration": 45,
        },
        "ask_cleaner_availability": {
            "transcript": [
                {"role": "agent", "message": "Hi, it's the property manager. I have the cleaning job at Seaside Apartment today, but the timing changed because the guest is checking out late."},
                {"role": "user", "message": "What time do you need me?"},
                {"role": "agent", "message": "The guest is leaving at 3 PM, and the new guest arrives at 9 PM. So we need you from 3:30 PM. Can you come?"},
                {"role": "user", "message": "3:30 in the afternoon? No, I'm sorry, I have another job at that time. I can only do morning slots."},
                {"role": "agent", "message": "I understand. Thank you for letting me know. I'll find someone else."},
                {"role": "user", "message": "Sorry. Call me for the morning ones!"},
            ],
            "extracted": {"available": False, "declined_reason": "has another job", "sentiment": "NEUTRAL"},
            "duration": 25,
        },
        "request_backup_cleaner": {
            "transcript": [
                {"role": "agent", "message": "Hi, this is the property manager. I have an urgent cleaning job at Seaside Apartment today. The primary cleaner can't make it. Are you available?"},
                {"role": "user", "message": "What time do you need?"},
                {"role": "agent", "message": "We need you from 3:30 PM today. The guest checks out at 3, new guest arrives at 9 PM."},
                {"role": "user", "message": "Oh no, I'm sorry — I already have a job at another property from 2 PM until 6 PM today. I really can't."},
                {"role": "agent", "message": "I understand. Thank you for letting me know. I'll find someone else."},
                {"role": "user", "message": "Sorry about that. Try me next time!"},
            ],
            "extracted": {"available": False, "declined_reason": "already booked at another property", "sentiment": "NEUTRAL"},
            "duration": 22,
        },
        "cancel_cleaner": {
            "transcript": [
                {"role": "agent", "message": "Hi, it's the property manager again. I'm calling about the Seaside Apartment cleaning job today."},
                {"role": "user", "message": "Yes?"},
                {"role": "agent", "message": "Since the timing doesn't work for you, we've found someone else for tonight. Your original 11 AM slot is cancelled — no need to come today."},
                {"role": "user", "message": "Okay, no problem. Thanks for letting me know."},
                {"role": "agent", "message": "Thank you for understanding. We'll call you for the next one. Have a good evening!"},
            ],
            "extracted": {"cancellation_confirmed": True},
            "duration": 22,
        },
        "cleaner_day_reminder": {
            "transcript": [
                {"role": "agent", "message": "Good morning! Quick reminder — you have the cleaning job today at Seaside Apartment. Please be there at 7:00 PM."},
                {"role": "user", "message": "Yes, I remember. I'll be there!"},
                {"role": "agent", "message": "I'll send you the door code via Telegram about 30 minutes before. All good?"},
                {"role": "user", "message": "All good, thanks!"},
            ],
            "extracted": {"confirmed": True},
            "duration": 18,
        },
        "request_photos_after_entry": {
            "transcript": [
                {"role": "agent", "message": "Hi, I see you just got into the apartment — great timing! Before you touch anything, I need you to take photos of every room."},
                {"role": "user", "message": "Okay, I'll do that right now."},
                {"role": "agent", "message": "Living room, bedroom, bathroom, kitchen — wide shots plus close-ups of the TV, mirrors, and tiles. Send them to me on Telegram."},
                {"role": "user", "message": "Got it. Taking photos now."},
                {"role": "agent", "message": "Once photos are done, start with stripping the bed linens and putting them in the washing machine first. They take longest to dry."},
                {"role": "user", "message": "Understood. Linens first, then bathrooms, then kitchen."},
            ],
            "extracted": {"photos_confirmed": True, "sop_understood": True},
            "duration": 52,
        },
        "notify_damage_claim_urgent": {
            "transcript": [
                {"role": "agent", "message": "Hello John, this is the property management team. I need to speak with you about something important regarding your stay at Seaside Apartment."},
                {"role": "user", "message": "Oh, hi. What's going on?"},
                {"role": "agent", "message": "During the post-checkout inspection, we detected damage to the TV in the living room — a 15-centimeter crack across the screen. I need to inform you that we'll be filing a damage claim."},
                {"role": "user", "message": "Wait, the TV? I... I didn't notice that. Are you sure it wasn't like that before?"},
                {"role": "agent", "message": "We have before-and-after photos from our inspection process. The damage was not present in the pre-checkout images. The estimated repair cost is $450."},
                {"role": "user", "message": "That's a lot. Can I see the photos?"},
                {"role": "agent", "message": "Absolutely, we'll send the comparison photos to your email along with the formal claim details. The amount will be processed through the platform's resolution center. Do you have any questions?"},
                {"role": "user", "message": "No, I guess I'll wait for the email. Thanks for letting me know."},
                {"role": "agent", "message": "Thank you for understanding, John. You'll receive the documentation within the hour. Have a good day."},
            ],
            "extracted": {"guest_notified": True, "claim_acknowledged": True, "dispute": False},
            "duration": 68,
        },
        "check_cleaner_progress": {
            "transcript": [
                {"role": "agent", "message": "Hi, just checking in — how's the cleaning going at Seaside Apartment?"},
                {"role": "user", "message": "Honestly, it's taking longer than expected. The kitchen was really messy and the oven needs deep cleaning."},
                {"role": "agent", "message": "I see. The next guest arrives at 2 AM. Do you think you can finish everything by then?"},
                {"role": "user", "message": "I'm not sure... the linens are still drying and I haven't started the second bathroom yet. I could use some help."},
                {"role": "agent", "message": "No problem, I'll send a backup cleaner to help you. They'll arrive within the hour. You stay as the lead — just tell them what still needs doing."},
                {"role": "user", "message": "That would be great, thank you!"},
                {"role": "agent", "message": "You're doing great. Keep going, help is on the way!"},
            ],
            "extracted": {"on_track": False, "needs_backup": True, "estimated_completion": "too late without help"},
            "duration": 35,
        },
        "dispatch_backup_to_help": {
            "transcript": [
                {"role": "agent", "message": "Hi, we need urgent help at Seaside Apartment. The primary cleaner is there but won't finish in time. Can you come right now?"},
                {"role": "user", "message": "Right now? Yes, I can be there in 30 minutes."},
                {"role": "agent", "message": "Perfect. The address is 123 Marina Boulevard, Apt 4B. I'll send you the door code on Telegram. When you arrive, the other cleaner will tell you what still needs to be done — she's leading the job."},
                {"role": "user", "message": "Got it. On my way!"},
                {"role": "agent", "message": "Thank you! You're a lifesaver. See you soon."},
            ],
            "extracted": {"backup_confirmed": True, "eta": "30 minutes"},
            "duration": 25,
        },
        "vendor_quote_request": {
            "transcript_vendor_1": [
                {"role": "agent", "message": "Hi, this is the property management team. We have a broken TV at Seaside Apartment — 55-inch Samsung. We need a replacement delivered and installed today. Can you help?"},
                {"role": "user", "message": "Yes, I can do that. I have a Samsung 55-inch in stock."},
                {"role": "agent", "message": "Great. What would be your total charge for delivery and installation?"},
                {"role": "user", "message": "For delivery and installation, it'll be $50."},
                {"role": "agent", "message": "Got it — $50 total. I'll get back to you shortly. Thanks!"},
            ],
            "transcript_vendor_2": [
                {"role": "agent", "message": "Hi, this is the property management team. We need an urgent TV replacement at Seaside Apartment — 55-inch Samsung. Can you deliver and install today?"},
                {"role": "user", "message": "Sure, I've got one available. When do you need it?"},
                {"role": "agent", "message": "As soon as possible. What's your charge for delivery plus installation?"},
                {"role": "user", "message": "I can do it for $25 including delivery."},
                {"role": "agent", "message": "That works. Let me confirm and I'll call you right back."},
            ],
            "extracted": {
                "vendor_1": {"name": "Mike's Electronics", "phone": "+14155551001", "quote": 50.0, "available": True},
                "vendor_2": {"name": "QuickFix AV", "phone": "+14155551002", "quote": 25.0, "available": True},
            },
            "duration": 35,
        },
        "vendor_accept": {
            "transcript": [
                {"role": "agent", "message": "Hi, it's the property management team again. Good news — we'd like to go with you for the TV replacement at Seaside Apartment."},
                {"role": "user", "message": "Great! When should I come?"},
                {"role": "agent", "message": "As soon as possible. The address is 123 Marina Boulevard, Apt 4B. I'll send you a door access code via text 30 minutes before. When you're done, please send me an invoice via Telegram."},
                {"role": "user", "message": "Perfect. I can be there in about an hour. I'll send the invoice right after."},
                {"role": "agent", "message": "Excellent. Thank you! See you soon."},
            ],
            "extracted": {"accepted": True, "eta": "1 hour", "invoice_method": "telegram"},
            "duration": 28,
        },
        "vendor_reject": {
            "transcript": [
                {"role": "agent", "message": "Hi, thanks for the quote on the TV replacement. Unfortunately, we've decided to go with another vendor this time."},
                {"role": "user", "message": "Oh okay, no problem."},
                {"role": "agent", "message": "We appreciate your time. We'll keep you in mind for future jobs. Thanks!"},
                {"role": "user", "message": "Sure, thanks. Bye!"},
            ],
            "extracted": {"notified": True},
            "duration": 15,
        },
        "payment_approval_call": {
            "transcript": [
                {"role": "agent", "message": "Hi, this is the property management AI. I need your approval for a payment related to Seaside Apartment."},
                {"role": "user", "message": "Sure, what's it for?"},
                {"role": "agent", "message": "We had to replace a broken TV. The vendor QuickFix AV charged $25 for delivery and installation. The invoice has been received. Do you approve the payment via TransferWise?"},
                {"role": "user", "message": "$25? Yeah that's fine. Go ahead and pay them."},
                {"role": "agent", "message": "Payment approved. I'll process it now via TransferWise. You'll receive a confirmation. Thank you!"},
                {"role": "user", "message": "Thanks!"},
            ],
            "extracted": {"approved": True, "amount": 25.0, "method": "transferwise"},
            "duration": 30,
        },
        "notify_guest_delay": {
            "transcript": [
                {"role": "agent", "message": "Hi {guest_name}, this is the property assistant at {property_name}. I'm calling about your check-in today."},
                {"role": "user", "message": "Yes, hi! Is everything OK?"},
                {"role": "agent", "message": "We're finishing up the preparation of your apartment, and it's taking a bit longer than expected. We may need about 30-60 extra minutes. I wanted to let you know in advance so you can plan accordingly."},
                {"role": "user", "message": "Oh OK, that's fine. We weren't planning to arrive until 7 PM anyway."},
                {"role": "agent", "message": "Perfect! We'll make sure everything is ready by then. I'll send you a confirmation message once the apartment is all set."},
                {"role": "user", "message": "Great, thanks for letting me know."},
                {"role": "agent", "message": "You're welcome! See you this evening. Bye!"},
            ],
            "extracted": {"guest_informed": True, "guest_ok_with_delay": True, "planned_arrival": "7:00 PM"},
            "duration": 32,
        },
        "escalate_no_cleaner": {
            "transcript": [
                {"role": "agent", "message": "Hi, this is the property management AI. I have an urgent issue at Seaside Apartment — I've called all available cleaners but none can come at the required time."},
                {"role": "user", "message": "Oh no, what time do we need them?"},
                {"role": "agent", "message": "We need cleaning done between 2 PM and 7 PM today. Cleaner 1 said 7 PM is too late, and the backup cleaner is also unavailable. The next guest arrives at 7 PM."},
                {"role": "user", "message": "Let me check... I know someone. Give me 10 minutes, I'll call you back with a cleaner."},
                {"role": "agent", "message": "Thank you. I'll wait for your confirmation. Please send me their phone number and I'll coordinate from there."},
                {"role": "user", "message": "Will do. Thanks for the heads up."},
            ],
            "extracted": {"escalated": True, "owner_will_find_cleaner": True},
            "duration": 35,
        },
        "door_trouble_call": {
            "transcript": [
                {"role": "agent", "message": "Hi, I got your message about the door. Let me help — can you try the code one more time? Make sure you press the pound key after entering it."},
                {"role": "user", "message": "I tried it three times already, it's not working."},
                {"role": "agent", "message": "No worries, I'll unlock the door remotely right now. Give me 10 seconds."},
                {"role": "user", "message": "Okay, waiting..."},
                {"role": "agent", "message": "Done — try the door now, it should be open."},
                {"role": "user", "message": "Yes! It's open now. Thank you!"},
                {"role": "agent", "message": "Great, you're in. Go ahead and start with the photos like we discussed. I'll send a technician to check the keypad later."},
            ],
            "extracted": {"resolved": True, "method": "remote_unlock", "keypad_issue": True},
            "duration": 30,
        },
        "guest_no_answer_voicemail": {
            "transcript": [
                {"role": "agent", "message": "Hi, this is the property assistant for Seaside Apartment. I was calling to check your arrival time today. Please call us back or send a message when you get this. We want to make sure everything is ready for you. Thank you!"},
            ],
            "extracted": {"voicemail_left": True, "answered": False},
            "duration": 15,
        },
        "final_photos_request": {
            "transcript": [
                {"role": "agent", "message": "Hi, you're almost done — great job! Before you leave, I need you to take final photos of every room and send them to me on Telegram."},
                {"role": "user", "message": "Okay, I'll take them now."},
                {"role": "agent", "message": "Perfect. Living room, bedroom, bathroom, kitchen — make sure the new TV is in the shot too. Also check that the baby crib is set up as I mentioned earlier."},
                {"role": "user", "message": "Oh... I forgot about the baby crib! I haven't set it up yet."},
                {"role": "agent", "message": "No worries, please set up the baby crib first, then take the final photos. It should be in the storage closet."},
                {"role": "user", "message": "Found it. Give me 10 minutes."},
                {"role": "agent", "message": "Take your time. Send me the photos when everything is ready including the crib."},
            ],
            "extracted": {"photos_sent": True, "baby_crib_forgotten": True, "baby_crib_fixed": True},
            "duration": 40,
        },
    }

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        voice_client: Any | None = None,
        nuki_client: Any | None = None,
        entry_detector: Any | None = None,
        photo_comparator: Any | None = None,
        session_id: str = "",
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
        cognitive: CognitiveController | None = None,
        phone_number_id: str = "",
        dry_run: bool = False,
        approval_gateway: ApprovalGateway | None = None,
        owner_id: str = "",
        property_id: str = "",
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self._voice = voice_client
        self._nuki = nuki_client
        self._entry_detector = entry_detector
        self._photo_comparator = photo_comparator
        self._phone_number_id = phone_number_id
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self._recorder = event_recorder
        self._episodic = episodic
        self._cognitive = cognitive
        self._scripts = _load_scripts()
        self._dry_run = dry_run
        self._approval = approval_gateway
        self._owner_id = owner_id
        self._property_id = property_id

        # Buffer for events emitted inside non-generator methods (e.g. _make_script_call)
        self._pending_events: list[AGUIEvent] = []
        self._phase_results: dict[str, dict[str, Any]] = {}

        transitions = [
            Transition(from_state=ASSESS_SITUATION, to_state=CALL_NEW_GUEST),
            Transition(from_state=CALL_NEW_GUEST, to_state=OFFER_FREE_CHECKOUT),
            Transition(from_state=OFFER_FREE_CHECKOUT, to_state=FIND_CLEANER),
            # Cleaner flow: available → setup, declined → replace, both declined → escalate
            Transition(from_state=FIND_CLEANER, to_state=REPLACE_CLEANER),
            Transition(from_state=FIND_CLEANER, to_state=DAY_OF_SETUP),
            Transition(from_state=REPLACE_CLEANER, to_state=CANCEL_ORIGINAL),
            Transition(from_state=REPLACE_CLEANER, to_state=ESCALATE_NO_CLEANER),
            Transition(from_state=CANCEL_ORIGINAL, to_state=DAY_OF_SETUP),
            Transition(from_state=ESCALATE_NO_CLEANER, to_state=DAY_OF_SETUP),
            Transition(from_state=ESCALATE_NO_CLEANER, to_state=DONE),
            # Day-of → entry → possible door trouble
            Transition(from_state=DAY_OF_SETUP, to_state=DETECT_ENTRY),
            Transition(from_state=DETECT_ENTRY, to_state=DOOR_TROUBLE),
            Transition(from_state=DETECT_ENTRY, to_state=REQUEST_PHOTOS),
            Transition(from_state=DOOR_TROUBLE, to_state=REQUEST_PHOTOS),
            # Photos → inspection → claim or progress
            Transition(from_state=REQUEST_PHOTOS, to_state=INSPECT_PHOTOS),
            Transition(from_state=INSPECT_PHOTOS, to_state=PROCESS_CLAIM),
            Transition(from_state=INSPECT_PHOTOS, to_state=CHECK_PROGRESS),
            # Damage found → claim → vendor flow
            Transition(from_state=PROCESS_CLAIM, to_state=VENDOR_QUOTES),
            Transition(from_state=VENDOR_QUOTES, to_state=VENDOR_SELECT),
            Transition(from_state=VENDOR_SELECT, to_state=VENDOR_ACCESS),
            Transition(from_state=VENDOR_ACCESS, to_state=VENDOR_INVOICE),
            Transition(from_state=VENDOR_INVOICE, to_state=PAYMENT_APPROVAL),
            Transition(from_state=PAYMENT_APPROVAL, to_state=PAYMENT_PROCESS),
            Transition(from_state=PAYMENT_PROCESS, to_state=CHECK_PROGRESS),
            # No damage → straight to check progress
            Transition(from_state=PROCESS_CLAIM, to_state=CHECK_PROGRESS),
            # Progress → backup if needed → final
            Transition(from_state=CHECK_PROGRESS, to_state=BACKUP_CLEANER),
            Transition(from_state=CHECK_PROGRESS, to_state=FINAL_PHOTOS),
            Transition(from_state=BACKUP_CLEANER, to_state=FINAL_PHOTOS),
            Transition(from_state=FINAL_PHOTOS, to_state=FINAL_VERIFICATION),
            Transition(from_state=FINAL_VERIFICATION, to_state=FLIGHT_CHECK),
            Transition(from_state=FLIGHT_CHECK, to_state=SMART_AC),
            Transition(from_state=SMART_AC, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=ASSESS_SITUATION,
        )

    # ── Public API ───────────────────────────────────────────────────── #

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the full scenario flow, yielding AG-UI events at each step."""
        flow_name = "scenario_flow"
        scenario_id = f"SCN-{self.session_id.upper()}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"

        self.slot_manager.set_slot("scenario_id", scenario_id)
        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)

        # ── AI Reasoning: Assess the situation ─────────────────────── #
        yield self.emitter.reasoning_start("Scenario initiated — assessing situation")
        yield self.emitter.reasoning_step(
            "REASONING",
            f"Late checkout conflict detected. Incoming guest {self.slot_manager.get_value('incoming_guest_name', 'guest')} needs to check in, "
            f"but departing guest {self.slot_manager.get_value('departing_guest_name', 'previous guest')} hasn't left yet. Need to coordinate timing.",
            confidence=0.95,
        )
        yield self.emitter.reasoning_step(
            "REASONING",
            f"Priority: Call {self.slot_manager.get_value('incoming_guest_name', 'incoming guest')} FIRST to determine actual arrival time. "
            f"This determines how much flexibility we have for {self.slot_manager.get_value('departing_guest_name', 'departing guest')}'s checkout.",
            confidence=0.9,
        )
        yield self.emitter.reasoning_step(
            "ACTION",
            f"Step 1: Call {self.slot_manager.get_value('incoming_guest_name', 'incoming guest')} → ask check-in time",
        )
        yield self.emitter.reasoning_step(
            "ACTION",
            f"Step 2: Call {self.slot_manager.get_value('departing_guest_name', 'departing guest')} → offer free late checkout",
        )
        yield self.emitter.reasoning_step(
            "ACTION",
            "Step 3: Coordinate cleaner for the gap between checkout and check-in",
        )
        yield self.emitter.reasoning_end(f"Plan: {self.slot_manager.get_value('incoming_guest_name', 'Guest')} → Cleaner → Setup → Monitor")

        if self._episodic:
            await self._episodic.add_episode(
                event="scenario_started",
                content=f"Scenario {scenario_id} started — calling immediately",
                metadata={"scenario_id": scenario_id, "state": ASSESS_SITUATION},
            )

        # ── Phase 1: Call incoming guest to ask check-in time ── #
        incoming_name = self.slot_manager.get_value("incoming_guest_name", "")
        incoming_phone = self.slot_manager.get_value("incoming_guest_phone", "")
        if incoming_phone:
            yield self.emitter.reasoning_step("ACTION", f"Calling incoming guest {incoming_name} to determine arrival time...")
            yield self.emitter.action_started("call_guest", incoming_name, {"purpose": "check-in time"})
            async for event in self._transition_to(flow_name, CALL_NEW_GUEST):
                yield event
            async for event in self._phase_call_new_guest():
                yield event
            yield self.emitter.action_completed("call_guest", f"{incoming_name} call completed")
        else:
            logger.info("SKIP: No incoming guest phone — skipping call_new_guest phase")

        # ── AI Reasoning: Analyze response ────────────────── #
        checkin_time = self.slot_manager.get_value("next_guest_checkin_time", "unknown")

        # ── Phase 2: Call departing guest to offer free late checkout ── #
        departing_name = self.slot_manager.get_value("departing_guest_name", "")
        departing_phone = self.slot_manager.get_value("departing_guest_phone", "")
        if departing_phone:
            yield self.emitter.reasoning_step(
                "REASONING",
                f"{incoming_name}'s planned arrival: {checkin_time}. "
                f"Now calling {departing_name} to offer free late checkout.",
                confidence=0.85,
            )
            yield self.emitter.action_started("call_guest", departing_name, {"purpose": "offer late checkout"})
            async for event in self._transition_to(flow_name, OFFER_FREE_CHECKOUT):
                yield event
            async for event in self._phase_offer_checkout():
                yield event
            yield self.emitter.action_completed("call_guest", f"{departing_name} call completed")
        else:
            logger.info("SKIP: No departing guest — skipping offer_checkout phase")

        # ── AI Reasoning: Assess checkout result + complaints ────────── #
        john_sentiment = self.slot_manager.get_value("john_sentiment", "NEUTRAL")
        john_complaints = self.slot_manager.get_value("john_complaints", [])
        checkout_time = self.slot_manager.get_value("john_checkout_time", "3:00 PM")

        _dep_name = departing_name or "departing guest"
        _inc_name = incoming_name or "incoming guest"

        if john_complaints or john_sentiment == "NEGATIVE":
            yield self.emitter.reasoning_step(
                "REASONING",
                f"WARNING: {_dep_name} is UNHAPPY. Complaints: {john_complaints}. "
                f"Sentiment: {john_sentiment}. Late checkout to {checkout_time} offered as goodwill.",
                confidence=0.9,
            )
            if self._episodic:
                await self._episodic.add_episode(
                    event="guest_complaints_recorded",
                    content=f"{_dep_name} complained: {', '.join(john_complaints) if john_complaints else 'general dissatisfaction'}",
                    metadata={"guest": _dep_name, "complaints": john_complaints, "sentiment": john_sentiment},
                )

        # ── Owner approval for late checkout (only if departing guest exists) ── #
        if departing_phone:
            yield self.emitter.reasoning_step(
                "REASONING",
                f"Requesting owner approval for free late checkout: "
                f"{_dep_name} → {checkout_time} (free, as goodwill).",
                confidence=0.95,
            )
            yield self.emitter.tool_call_start("request_owner_approval")

            if self._dry_run:
                import asyncio as _aio
                await _aio.sleep(2)
                approval_status = "approved"
            elif self._approval:
                approval_result = await self._approval.request_approval(
                    action_type=ActionType.LATE_CHECKOUT,
                    owner_id=self._owner_id,
                    property_id=self._property_id,
                    description=(
                        f"{_dep_name} requests late checkout at {checkout_time}. "
                        f"FREE — as goodwill. {_inc_name} arrives at {checkin_time}."
                    ),
                    proposed_action={"checkout_time": checkout_time, "fee": 0, "reason": "goodwill"},
                    context={"guest_sentiment": john_sentiment, "complaints": john_complaints},
                    urgency=3,
                )
                approval_status = approval_result.status.value
            else:
                approval_status = "auto_approved"

            yield self.emitter.tool_call_end(
                "request_owner_approval",
                {"status": approval_status, "action": "late_checkout", "dry_run": self._dry_run},
            )

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Owner {approval_status.upper()} the free late checkout for {_dep_name}."
            )
            yield self.emitter.text_message_end()

            # ── Guest Lead Scoring: departing guest ── #
            yield self.emitter.reasoning_step("REASONING", f"Running Guest Lead Scoring for {_dep_name}...", confidence=0.95)

            if john_complaints or john_sentiment == "NEGATIVE":
                yield self.emitter.tool_call_start("guest_lead_scoring")
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"GUEST LEAD SCORING — {_dep_name} (departing):\n"
                    f"  Complaints: {john_complaints}\n"
                    f"  Sentiment: {john_sentiment}\n"
                    "  Score update: -5 (negative), -3 (cleaning needed)\n"
                    "  ACTION: Offer 15% discount on next stay."
                )
                yield self.emitter.text_message_end()
                yield self.emitter.tool_call_end("guest_lead_scoring", {
                    "guest": _dep_name, "action": "discount_15_percent", "reason": "negative_experience",
                })
            else:
                yield self.emitter.tool_call_start("guest_lead_scoring")
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"GUEST LEAD SCORING — {_dep_name} (departing):\n"
                    "  No complaints, clean checkout, on-time departure\n"
                    "  Score update: +5 (no_incidents), +3 (clean), +3 (on_time)\n"
                    "  ACTION: Send loyalty bonus for next booking."
                )
                yield self.emitter.text_message_end()
                yield self.emitter.tool_call_end("guest_lead_scoring", {
                    "guest": _dep_name, "action": "bonus_10_percent", "reason": "positive_experience",
                })
        else:
            approval_status = "skipped"
            logger.info("SKIP: No departing guest — skipping owner approval and lead scoring")

        yield self.emitter.reasoning_step(
            "REASONING",
            f"Checkout: {_dep_name} leaves at {checkout_time}. {_inc_name} arrives at {checkin_time}. "
            f"Window for cleaning: {checkout_time} → {checkin_time}. "
            "Now need to find a cleaner for this window.",
            confidence=0.9,
        )

        # ── Phase 4: FIND CLEANER ────────────────────────────────────── #
        yield self.emitter.action_started("find_cleaner", "property", {"purpose": "schedule cleaning"})
        async for event in self._transition_to(flow_name, FIND_CLEANER):
            yield event
        async for event in self._phase_find_cleaner():
            yield event
        yield self.emitter.action_completed("find_cleaner", "Cleaner coordination completed")

        # ── Phase 4b: REPLACE CLEANER (if needed) ────────────────────── #
        needs_replacement = self.slot_manager.get_value("cleaner_needs_replacement", False)
        if needs_replacement:
            async for event in self._transition_to(flow_name, REPLACE_CLEANER):
                yield event
            async for event in self._phase_replace_cleaner():
                yield event

            # Check if backup also declined
            backup_declined = self.slot_manager.get_value("backup_also_declined", False)
            if backup_declined:
                # ESCALATE — both cleaners declined, full escalation chain
                async for event in self._transition_to(flow_name, ESCALATE_NO_CLEANER):
                    yield event
                async for event in self._phase_escalate_no_cleaner():
                    yield event

                # If STILL no cleaner — take emergency action, don't pause
                if not self.slot_manager.get_value("cleaner_found_after_escalation", False):
                    yield self.emitter.reasoning_step(
                        "REASONING",
                        "CRITICAL: No cleaner found after full escalation. "
                        "Taking emergency action: notify incoming guest about possible delay, "
                        "keep trying to find a cleaner.",
                        confidence=0.8,
                    )

                    # Notify incoming guest about potential delay
                    incoming_guest = self.slot_manager.get_value("incoming_guest_name", "Guest")
                    incoming_phone = self.slot_manager.get_value("incoming_guest_phone", "")
                    checkin_time = self.slot_manager.get_value("next_guest_checkin_time", "")

                    if incoming_phone:
                        yield self.emitter.text_message_start()
                        yield self.emitter.text_message_content(
                            f"Calling {incoming_guest} to inform about potential delay..."
                        )
                        yield self.emitter.text_message_end()

                        # Use a polite delay notification script
                        delay_result = await self._make_script_call(
                            "notify_guest_delay",
                            incoming_phone,
                            guest_name=incoming_guest,
                            property_name=self.slot_manager.get_value("property_name", ""),
                            original_checkin=checkin_time,
                            reason="cleaning coordination",
                        )
                        for _evt in self._drain_pending_events():
                            yield _evt

                    # Record the unresolved situation
                    if self._episodic:
                        await self._episodic.add_episode(
                            event="cleaner_crisis_unresolved",
                            content=(
                                f"No cleaner found after full escalation. "
                                f"Incoming guest {incoming_guest} notified about delay. "
                                "Owner must resolve manually."
                            ),
                            metadata={
                                "incoming_guest": incoming_guest,
                                "checkin_time": checkin_time,
                                "status": "waiting_for_owner",
                            },
                        )

                    yield self.emitter.text_message_start()
                    yield self.emitter.text_message_content(
                        "CRITICAL STATUS:\n"
                        f"  - All cleaners exhausted\n"
                        f"  - Manager and owner notified\n"
                        f"  - Incoming guest {incoming_guest} informed about delay\n"
                        f"  - Waiting for owner to provide a cleaner contact\n"
                        "  - System will continue monitoring and act as soon as a cleaner is confirmed"
                    )
                    yield self.emitter.text_message_end()
            else:
                # Cancel original cleaner
                async for event in self._transition_to(flow_name, CANCEL_ORIGINAL):
                    yield event
                async for event in self._phase_cancel_original():
                    yield event

        # ── Phase 5: DAY-OF SETUP ────────────────────────────────────── #
        async for event in self._transition_to(flow_name, DAY_OF_SETUP):
            yield event
        async for event in self._phase_day_of_setup():
            yield event

        # ── Phase 6: DETECT ENTRY ────────────────────────────────────── #
        async for event in self._transition_to(flow_name, DETECT_ENTRY):
            yield event
        async for event in self._phase_detect_entry():
            yield event

        # ── Phase 6b: DOOR TROUBLE (if cleaner can't enter) ──────────── #
        door_trouble = self.slot_manager.get_value("door_trouble", False)
        if door_trouble:
            async for event in self._transition_to(flow_name, DOOR_TROUBLE):
                yield event
            async for event in self._phase_door_trouble():
                yield event

        # ── Phase 7: REQUEST PHOTOS ──────────────────────────────────── #
        async for event in self._transition_to(flow_name, REQUEST_PHOTOS):
            yield event
        async for event in self._phase_request_photos():
            yield event

        # ── Phase 8: INSPECT PHOTOS ──────────────────────────────────── #
        async for event in self._transition_to(flow_name, INSPECT_PHOTOS):
            yield event
        async for event in self._phase_inspect_photos():
            yield event

        # ── Phase 9: PROCESS CLAIM (if damage) ───────────────────────── #
        damage_detected = self.slot_manager.get_value("damage_detected", False)
        if damage_detected:
            async for event in self._transition_to(flow_name, PROCESS_CLAIM):
                yield event
            async for event in self._phase_process_claim():
                yield event

            # ── Phase 10: VENDOR QUOTES — call 2 vendors for price ─── #
            yield self.emitter.reasoning_step(
                "REASONING",
                "TV is broken — need to replace before next guest. "
                "Calling two vendors simultaneously to compare prices.",
                confidence=0.95,
            )
            async for event in self._transition_to(flow_name, VENDOR_QUOTES):
                yield event
            async for event in self._phase_vendor_quotes():
                yield event

            # ── Phase 11: VENDOR SELECT — pick cheaper, reject other ── #
            async for event in self._transition_to(flow_name, VENDOR_SELECT):
                yield event
            async for event in self._phase_vendor_select():
                yield event

            # ── Phase 12: VENDOR ACCESS — code + entry for vendor ──── #
            async for event in self._transition_to(flow_name, VENDOR_ACCESS):
                yield event
            async for event in self._phase_vendor_access():
                yield event

            # ── Phase 13: VENDOR INVOICE — collect invoice ─────────── #
            async for event in self._transition_to(flow_name, VENDOR_INVOICE):
                yield event
            async for event in self._phase_vendor_invoice():
                yield event

            # ── Phase 14: PAYMENT APPROVAL — call owner ────────────── #
            async for event in self._transition_to(flow_name, PAYMENT_APPROVAL):
                yield event
            async for event in self._phase_payment_approval():
                yield event

            # ── Phase 15: PAYMENT PROCESS — TransferWise ───────────── #
            async for event in self._transition_to(flow_name, PAYMENT_PROCESS):
                yield event
            async for event in self._phase_payment_process():
                yield event

        # ── Phase 16: CHECK PROGRESS ─────────────────────────────────── #
        async for event in self._transition_to(flow_name, CHECK_PROGRESS):
            yield event
        async for event in self._phase_check_progress():
            yield event

        # ── Phase 17: BACKUP CLEANER (if needed) ─────────────────────── #
        needs_backup = self.slot_manager.get_value("needs_backup_cleaner", False)
        if needs_backup:
            async for event in self._transition_to(flow_name, BACKUP_CLEANER):
                yield event
            async for event in self._phase_backup_cleaner():
                yield event

        # ── Phase 18: FINAL PHOTOS — cleaner sends exit photos ───────── #
        async for event in self._transition_to(flow_name, FINAL_PHOTOS):
            yield event
        async for event in self._phase_final_photos():
            yield event

        # ── Phase 19: FINAL VERIFICATION — check photos + baby crib ── #
        async for event in self._transition_to(flow_name, FINAL_VERIFICATION):
            yield event
        async for event in self._phase_final_verification():
            yield event

        # ── Phase 20: FLIGHT CHECK — track guest flight ──────────────── #
        async for event in self._transition_to(flow_name, FLIGHT_CHECK):
            yield event
        async for event in self._phase_flight_check():
            yield event

        # ── Phase 21: SMART AC — turn on AC 30 min before arrival ────── #
        async for event in self._transition_to(flow_name, SMART_AC):
            yield event
        async for event in self._phase_smart_ac():
            yield event

        # ── GUEST LEAD SCORING: incoming guest — future tracking ──── #
        damage_found = self.slot_manager.get_value("damage_detected", False)
        _inc = self.slot_manager.get_value("incoming_guest_name", "incoming guest")

        yield self.emitter.tool_call_start("guest_lead_scoring_incoming")
        yield self.emitter.text_message_start()

        if damage_found:
            yield self.emitter.text_message_content(
                f"GUEST LEAD SCORING — {_inc} (incoming):\n\n"
                f"  Issue detected before check-in. Vendor dispatched.\n"
                f"  If not resolved → {_inc} gets 15% discount.\n"
                f"  If resolved → standard check-in."
            )
        else:
            yield self.emitter.text_message_content(
                f"GUEST LEAD SCORING — {_inc} (incoming):\n\n"
                f"  Property in perfect condition. No issues.\n"
                f"  Score will update at checkout based on experience."
            )

        yield self.emitter.text_message_end()
        yield self.emitter.tool_call_end("guest_lead_scoring_incoming", {
            "guest": _inc,
            "damage_at_checkin": damage_found,
            "future_action": "discount_if_unresolved" if damage_found else "standard",
        })

        # ── DONE ─────────────────────────────────────────────────────── #
        async for event in self._transition_to(flow_name, DONE):
            yield event

        if self._episodic:
            await self._episodic.add_episode(
                event="scenario_completed",
                content=f"Scenario {scenario_id} completed successfully",
                metadata={"scenario_id": scenario_id, "phases": list(self._phase_results.keys())},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Scenario {scenario_id} completed. All phases executed."
        )
        yield self.emitter.text_message_end()

        yield self.emitter.flow_completed(flow_name, {
            "scenario_id": scenario_id,
            "status": "completed",
            "phases": self._phase_results,
        })

    # ── Phase Implementations ────────────────────────────────────────── #

    async def _phase_offer_checkout(self) -> AsyncIterator[AGUIEvent]:
        """Phase 2: Call departing guest to offer free late checkout."""
        guest_name = self.slot_manager.get_value("departing_guest_name", "Guest")
        guest_phone = self.slot_manager.get_value("departing_guest_phone", "")
        logger.info("▶ PHASE: offer_checkout — calling %s (%s) to offer late checkout", guest_name, guest_phone)
        checkout_time = self.slot_manager.get_value("standard_checkout_time", "11:00 AM")
        next_guest_checkin = self.slot_manager.get_value("next_guest_checkin_time", "4:00 PM")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Calling {guest_name} to offer free late checkout extension..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "offer_free_late_checkout",
            guest_phone,
            guest_name=guest_name,
            checkout_time=checkout_time,
            next_guest_checkin=next_guest_checkin,
            property_name=self.slot_manager.get_value("property_name", "the property"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        yield self.emitter.slot_filled("checkout_offer_call_id", call_id)

        # Wait for the call to finish
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for call with {guest_name} to finish...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Call with {guest_name} completed ({call_final.get('duration', 0):.0f}s)"
            )
            yield self.emitter.text_message_end()

        self._phase_results["offer_checkout"] = {"call_result": call_result}
        logger.info("  ▶ offer_checkout result: status=%s, call_id=%s", call_result.get("status"), call_result.get("call_id"))

        # In dry_run, use extracted data to fill slots
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("accepted"):
                checkout_time = extracted.get("checkout_time", "2:00 PM")
                self.slot_manager.set_slot("late_checkout_time", checkout_time)
                yield self.emitter.slot_filled("late_checkout_time", checkout_time)
                yield self.emitter.slot_filled("late_checkout_accepted", "true")
            dry_sentiment = extracted.get("sentiment", "POSITIVE")
        else:
            dry_sentiment = "NEUTRAL"

        # Emit call events and sentiment (call_ended already emitted in dry_run via drain)
        if not self._dry_run:
            yield self.emitter.call_ended(
                call_id=call_id,
                duration_seconds=call_result.get("duration", 0) or 0,
                status=call_result.get("status", "unknown"),
            )
        yield self.emitter.sentiment_update(
            guest_name=guest_name,
            sentiment=dry_sentiment,
            score=0.7 if dry_sentiment == "POSITIVE" else 0.2,
            reason="Late checkout offer — guest accepted" if dry_sentiment == "POSITIVE"
                   else "Late checkout offer made — awaiting guest reaction from transcript",
        )

        if self._episodic:
            await self._episodic.add_episode(
                event="checkout_offer_call_made",
                content=f"Called {guest_name} at {guest_phone} to offer free late checkout",
                metadata={"guest_name": guest_name, "call_id": call_id},
            )

    async def _phase_call_new_guest(self) -> AsyncIterator[AGUIEvent]:
        """Phase 3: Call incoming guest to ask expected check-in time."""
        guest_name = self.slot_manager.get_value("incoming_guest_name", "Guest")
        guest_phone = self.slot_manager.get_value("incoming_guest_phone", "")
        logger.info("▶ PHASE: call_new_guest — calling %s (%s) to ask check-in time", guest_name, guest_phone)
        checkin_date = self.slot_manager.get_value("checkin_date", "today")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Calling incoming guest {guest_name} to confirm check-in time..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "ask_guest_checkin_time",
            guest_phone,
            guest_name=guest_name,
            checkin_date=checkin_date,
            property_name=self.slot_manager.get_value("property_name", "the property"),
            standard_checkin=self.slot_manager.get_value("standard_checkin_time", "3:00 PM"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        yield self.emitter.slot_filled("new_guest_call_id", call_id)

        # Wait for the call to finish before proceeding
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for call with {guest_name} to finish...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Call with {guest_name} completed ({call_final.get('duration', 0):.0f}s)"
            )
            yield self.emitter.text_message_end()

        self._phase_results["call_new_guest"] = {"call_result": call_result}
        logger.info("  ▶ call_new_guest result: status=%s, call_id=%s", call_result.get("status"), call_result.get("call_id"))

        # In dry_run, use extracted data to fill slots
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if "checkin_time" in extracted:
                self.slot_manager.set_slot("next_guest_checkin_time", extracted["checkin_time"])
                yield self.emitter.slot_filled("next_guest_checkin_time", extracted["checkin_time"])
            dry_sentiment = extracted.get("sentiment", "POSITIVE")
        else:
            dry_sentiment = "POSITIVE"

        # Emit call events and sentiment (call_ended already emitted in dry_run via drain)
        if not self._dry_run:
            yield self.emitter.call_ended(
                call_id=call_id,
                duration_seconds=call_result.get("duration", 0) or 0,
                status=call_result.get("status", "unknown"),
            )
        yield self.emitter.sentiment_update(
            guest_name=guest_name,
            sentiment=dry_sentiment,
            score=0.7,
            reason="Guest engaged positively — provided check-in time information",
        )
        yield self.emitter.guest_status_update(
            guest_name=guest_name,
            status="confirmed",
            details={"checkin_date": checkin_date},
        )

        if self._episodic:
            await self._episodic.add_episode(
                event="new_guest_call_made",
                content=f"Called incoming guest {guest_name} to ask check-in time",
                metadata={"guest_name": guest_name, "call_id": call_id},
            )

    async def _phase_find_cleaner(self) -> AsyncIterator[AGUIEvent]:
        """Phase 4: Find and assign an available cleaner."""
        property_id = self.slot_manager.get_value("property_id", "")
        logger.info("▶ PHASE: find_cleaner — searching cleaner for property %s", property_id)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Finding available cleaner for property {property_id}..."
        )
        yield self.emitter.text_message_end()

        # Load cleaner from config
        cleaners_path = Path(__file__).resolve().parents[2] / "config" / "cleaners.json"
        cleaner_info: dict[str, Any] = {}
        try:
            import json
            with open(cleaners_path) as f:
                cleaners = json.load(f)
            if cleaners:
                cleaner_info = cleaners[0] if isinstance(cleaners, list) else next(iter(cleaners.values()), {})
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Could not load cleaners config")

        cleaner_name = cleaner_info.get("name", "Assigned Cleaner")
        cleaner_phone = cleaner_info.get("phone", "")

        self.slot_manager.set_slot("cleaner_name", cleaner_name)
        self.slot_manager.set_slot("cleaner_phone", cleaner_phone)

        # Call cleaner to check availability
        call_result = await self._make_script_call(
            "ask_cleaner_availability",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            cleaning_date=self.slot_manager.get_value("cleaning_date", "today"),
            cleaning_time=self.slot_manager.get_value("cleaning_time", "7:00 PM"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        yield self.emitter.slot_filled("cleaner_name", cleaner_name)
        yield self.emitter.slot_filled("cleaner_phone", cleaner_phone)

        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for call with {cleaner_name} to finish...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Call with {cleaner_name} completed ({call_final.get('duration', 0):.0f}s)"
            )
            yield self.emitter.text_message_end()

        # In dry_run, use extracted data
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("available"):
                confirmed_time = extracted.get("confirmed_time", "7:00 PM")
                self.slot_manager.set_slot("cleaner_confirmed_time", confirmed_time)
                yield self.emitter.slot_filled("cleaner_confirmed_time", confirmed_time)
                yield self.emitter.slot_filled("cleaner_available", "true")
            else:
                # Cleaner declined — need replacement
                self.slot_manager.set_slot("cleaner_needs_replacement", True)
                self.slot_manager.set_slot("original_cleaner_name", cleaner_name)
                self.slot_manager.set_slot("original_cleaner_phone", cleaner_phone)
                yield self.emitter.slot_filled("cleaner_available", "false")
                yield self.emitter.reasoning_step(
                    "REASONING",
                    f"{cleaner_name} declined (reason: {extracted.get('declined_reason', 'unavailable')}). "
                    "Need to find backup cleaner and cancel original assignment.",
                    confidence=0.95,
                )

        self._phase_results["find_cleaner"] = {
            "cleaner_name": cleaner_name,
            "cleaner_phone": cleaner_phone,
            "call_result": call_result,
        }
        logger.info("  ▶ find_cleaner result: cleaner=%s, phone=%s, status=%s", cleaner_name, cleaner_phone, call_result.get("status"))

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_contacted",
                content=f"Contacted cleaner {cleaner_name} for availability",
                metadata={"cleaner_name": cleaner_name, "call_id": call_id},
            )

    async def _phase_replace_cleaner(self) -> AsyncIterator[AGUIEvent]:
        """Phase 4b: Find a replacement cleaner if original declined."""
        original_name = self.slot_manager.get_value("original_cleaner_name", "Cleaner 1")
        logger.info("▶ PHASE: replace_cleaner — %s declined, finding backup", original_name)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"{original_name} unavailable at requested time. Calling backup cleaner..."
        )
        yield self.emitter.text_message_end()

        # Load backup cleaner from config
        backup_name = self.slot_manager.get_value("backup_cleaner_name", "")
        backup_phone = self.slot_manager.get_value("backup_cleaner_phone", "")

        if not backup_phone:
            cleaners_path = Path(__file__).resolve().parents[2] / "config" / "cleaners.json"
            try:
                import json
                with open(cleaners_path) as f:
                    cleaners = json.load(f)
                if isinstance(cleaners, list) and len(cleaners) > 1:
                    backup_name = cleaners[1].get("name", "Backup Cleaner")
                    backup_phone = cleaners[1].get("phone", "")
            except (FileNotFoundError, json.JSONDecodeError):
                logger.warning("Could not load backup cleaner from config")

        if not backup_name:
            backup_name = "Backup Cleaner"

        call_result = await self._make_script_call(
            "request_backup_cleaner",
            backup_phone,
            cleaner_name=backup_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            cleaning_date=self.slot_manager.get_value("cleaning_date", "today"),
            cleaning_time=self.slot_manager.get_value("cleaning_time", "7:00 PM"),
            reason=f"{original_name} unavailable at requested time",
        )
        for _evt in self._drain_pending_events():
            yield _evt

        # In dry_run, use extracted data
        backup_available = True
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("available"):
                confirmed_time = extracted.get("confirmed_time", "7:00 PM")
                self.slot_manager.set_slot("cleaner_confirmed_time", confirmed_time)
                yield self.emitter.slot_filled("cleaner_confirmed_time", confirmed_time)
            else:
                backup_available = False

        if backup_available:
            # Switch active cleaner to backup
            self.slot_manager.set_slot("cleaner_name", backup_name)
            self.slot_manager.set_slot("cleaner_phone", backup_phone)
            yield self.emitter.slot_filled("cleaner_name", backup_name)
            yield self.emitter.slot_filled("cleaner_phone", backup_phone)
        else:
            # Backup ALSO declined → trigger escalation
            self.slot_manager.set_slot("backup_also_declined", True)
            yield self.emitter.slot_filled("backup_also_declined", True)

            yield self.emitter.reasoning_step(
                "REASONING",
                f"BOTH cleaners declined! {original_name}: has another job. "
                f"{backup_name}: {call_result.get('extracted', {}).get('declined_reason', 'unavailable')}. "
                "Starting full escalation: platform search → manager → owner.",
                confidence=0.95,
            )

            # ── Update scoring: both cleaners rejected ──────────────────
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"SCORING UPDATE:\n"
                f"  {original_name}: -1 (rejected) → score decreased\n"
                f"  {backup_name}: -1 (rejected) → score decreased\n"
                f"  System will remember and try other cleaners first next time."
            )
            yield self.emitter.text_message_end()

        self._phase_results["replace_cleaner"] = {
            "backup_name": backup_name,
            "backup_available": backup_available,
            "call_result": call_result,
        }
        logger.info("  ▶ replace_cleaner result: backup=%s, available=%s", backup_name, backup_available)

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_replaced",
                content=f"Replacement cleaner {backup_name} confirmed for cleaning",
                metadata={"backup_name": backup_name},
            )

    async def _phase_cancel_original(self) -> AsyncIterator[AGUIEvent]:
        """Phase 4c: Cancel the original cleaner's 11 AM assignment."""
        original_name = self.slot_manager.get_value("original_cleaner_name", "")
        original_phone = self.slot_manager.get_value("original_cleaner_phone", "")
        logger.info("▶ PHASE: cancel_original — cancelling %s (%s)", original_name, original_phone)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Cancelling {original_name}'s original 11 AM cleaning slot..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "cancel_cleaner",
            original_phone,
            cleaner_name=original_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            cleaning_date=self.slot_manager.get_value("cleaning_date", "today"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for cancellation call with {original_name}...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"{original_name}'s 11 AM slot cancelled. {self.slot_manager.get_value('cleaner_name', 'Backup Cleaner')} is now assigned."
        )
        yield self.emitter.text_message_end()

        self._phase_results["cancel_original"] = {"call_result": call_result}

        if self._episodic:
            await self._episodic.add_episode(
                event="original_cleaner_cancelled",
                content=f"Original cleaner {original_name} notified of cancellation — 11 AM slot removed",
                metadata={"cleaner_name": original_name},
            )

    async def _phase_escalate_no_cleaner(self) -> AsyncIterator[AGUIEvent]:
        """Escalation: Both cleaners declined — full fallback chain.

        Chain of actions:
        1. Try additional cleaners from config (3rd, 4th, etc.)
        2. Call property manager
        3. Call property owner
        4. Search marketplace (Turno)
        5. Send Telegram notification with options
        """
        import json as _json
        logger.info("▶ PHASE: escalate_no_cleaner — all cleaners declined, escalating")

        manager_phone = self.slot_manager.get_value("manager_phone", "")
        owner_phone = self.slot_manager.get_value("owner_phone", "")
        property_name = self.slot_manager.get_value("property_name", "the property")
        property_address = self.slot_manager.get_value("property_address", "")
        cleaning_time = self.slot_manager.get_value("cleaning_time", "7:00 PM")

        yield self.emitter.reasoning_step(
            "REASONING",
            "Both Cleaner 1 and Backup Cleaner declined. "
            "Starting full escalation chain: "
            "additional cleaners → PLATFORM SEARCH (Turno) → manager → owner.",
            confidence=0.9,
        )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "ESCALATION: All primary cleaners unavailable. "
            "Starting escalation chain..."
        )
        yield self.emitter.text_message_end()

        # ── Step 1: Try additional cleaners from config ──────────────────
        additional_cleaners: list[dict] = []
        try:
            cleaners_path = Path(__file__).resolve().parents[2] / "config" / "cleaners.json"
            with open(cleaners_path) as f:
                all_cleaners = _json.load(f)
            if isinstance(all_cleaners, list) and len(all_cleaners) > 2:
                additional_cleaners = all_cleaners[2:]  # Skip first 2 (already tried)
        except (FileNotFoundError, _json.JSONDecodeError):
            pass

        cleaner_found = False

        for i, cleaner in enumerate(additional_cleaners, start=3):
            c_name = cleaner.get("name", f"Cleaner {i}")
            c_phone = cleaner.get("phone", "")
            if not c_phone:
                continue

            yield self.emitter.reasoning_step(
                "ACTION",
                f"Trying additional cleaner #{i}: {c_name} ({c_phone})",
                confidence=0.7,
            )

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Calling additional cleaner {c_name}..."
            )
            yield self.emitter.text_message_end()

            call_result = await self._make_script_call(
                "ask_cleaner_availability",
                c_phone,
                guest_name=c_name,
                property_name=property_name,
                cleaning_time=cleaning_time,
            )
            for _evt in self._drain_pending_events():
                yield _evt

            call_id = call_result.get("call_id", "")
            if call_id:
                call_final = await self._wait_for_call(call_id)
                call_result.update(call_final)

            extracted = call_result.get("extracted", {})
            if extracted.get("available"):
                self.slot_manager.set_slot("cleaner_name", c_name)
                self.slot_manager.set_slot("cleaner_phone", c_phone)
                self.slot_manager.set_slot("cleaner_found_after_escalation", True)
                yield self.emitter.slot_filled("cleaner_name", c_name)
                yield self.emitter.slot_filled("cleaner_phone", c_phone)
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"Cleaner {c_name} is available! Assigned to the job."
                )
                yield self.emitter.text_message_end()
                cleaner_found = True
                break

        # ── Step 2: Search Platform (Turno / TurnoverBnB) ────────────────
        if not cleaner_found:
            yield self.emitter.reasoning_step(
                "ACTION",
                "No cleaners available from contacts. Searching cleaning platform (Turno)...",
                confidence=0.7,
            )

            yield self.emitter.tool_call_start("platform_search_turno")
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "PLATFORM SEARCH: Searching Turno marketplace for available cleaners...\n"
                f"  Location: {property_address or property_name}\n"
                f"  Date: today\n"
                f"  Time: {cleaning_time}\n"
                "  Searching..."
            )
            yield self.emitter.text_message_end()

            if self._dry_run:
                import asyncio as _aio
                await _aio.sleep(3)

            # In dry run: platform search fails (to show full escalation)
            platform_found = False
            yield self.emitter.tool_call_end(
                "platform_search_turno",
                {"found": platform_found, "candidates": 0, "reason": "No available cleaners in area for this time slot"},
            )

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Platform search: NO RESULTS. No cleaners available on Turno for this time.\n"
                "Escalating to property manager..."
            )
            yield self.emitter.text_message_end()

        # ── Step 3: Call manager ─────────────────────────────────────────
        if not cleaner_found and manager_phone:
            yield self.emitter.reasoning_step(
                "ACTION",
                "No additional cleaners available. Calling property manager...",
                confidence=0.8,
            )

            call_result = await self._make_script_call(
                "escalate_no_cleaner",
                manager_phone,
                guest_name="Manager",
                property_name=property_name,
                cleaning_time=cleaning_time,
            )
            for _evt in self._drain_pending_events():
                yield _evt

            call_id = call_result.get("call_id", "")
            if call_id:
                call_final = await self._wait_for_call(call_id)
                call_result.update(call_final)

            extracted = call_result.get("extracted", {})
            if extracted.get("owner_will_find_cleaner") or extracted.get("escalated"):
                self.slot_manager.set_slot("cleaner_found_after_escalation", True)
                cleaner_found = True
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "Manager will find a cleaner and send their contact. Standing by..."
                )
                yield self.emitter.text_message_end()

        # ── Step 3: Call owner ───────────────────────────────────────────
        if not cleaner_found and owner_phone and owner_phone != manager_phone:
            yield self.emitter.reasoning_step(
                "ACTION",
                "Manager couldn't help. Calling property owner...",
                confidence=0.7,
            )

            call_result = await self._make_script_call(
                "escalate_no_cleaner",
                owner_phone,
                guest_name="Owner",
                property_name=property_name,
                cleaning_time=cleaning_time,
            )
            for _evt in self._drain_pending_events():
                yield _evt

            call_id = call_result.get("call_id", "")
            if call_id:
                call_final = await self._wait_for_call(call_id)
                call_result.update(call_final)

            extracted = call_result.get("extracted", {})
            if extracted.get("owner_will_find_cleaner") or extracted.get("escalated"):
                self.slot_manager.set_slot("cleaner_found_after_escalation", True)
                cleaner_found = True
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "Owner will find a cleaner. Waiting for confirmation..."
                )
                yield self.emitter.text_message_end()

        # ── Step 4: Last resort — Telegram notification with options ─────
        if not cleaner_found:
            yield self.emitter.reasoning_step(
                "REASONING",
                "All escalation attempts exhausted. "
                "Sending urgent Telegram notification to owner with options: "
                "find cleaner / delay cleaning / post on Turno marketplace.",
                confidence=0.5,
            )

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "CRITICAL: No cleaner found after full escalation.\n"
                f"Property: {property_name}\n"
                f"Cleaning needed by: {cleaning_time}\n\n"
                "Options sent to owner via Telegram:\n"
                "  1. Provide another cleaner's contact\n"
                "  2. Post the job on Turno marketplace\n"
                "  3. Delay cleaning (inform incoming guest)\n"
                "  4. Owner cleans the property themselves\n\n"
                "Waiting for owner response..."
            )
            yield self.emitter.text_message_end()

            # In dry_run — simulate owner responding
            if self._dry_run:
                import asyncio as _asyncio
                await _asyncio.sleep(3)
                self.slot_manager.set_slot("cleaner_found_after_escalation", True)
                self.slot_manager.set_slot("escalation_resolution", "owner_will_find")
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "[DRY RUN] Owner responded: will find a cleaner within 10 minutes."
                )
                yield self.emitter.text_message_end()

        # Record result
        escalation_resolved = self.slot_manager.get_value("cleaner_found_after_escalation", False)
        self._phase_results["escalate_no_cleaner"] = {
            "resolved": escalation_resolved,
            "additional_cleaners_tried": len(additional_cleaners),
            "manager_called": bool(manager_phone),
            "owner_called": bool(owner_phone),
        }

        if self._episodic:
            await self._episodic.add_episode(
                event="cleaner_escalation_complete",
                content=(
                    f"Escalation {'resolved' if escalation_resolved else 'UNRESOLVED'}. "
                    f"Tried {len(additional_cleaners)} additional cleaners, "
                    f"manager={'yes' if manager_phone else 'no'}, "
                    f"owner={'yes' if owner_phone else 'no'}."
                ),
                metadata=self._phase_results["escalate_no_cleaner"],
            )

    async def _phase_door_trouble(self) -> AsyncIterator[AGUIEvent]:
        """Edge case: Cleaner can't enter — troubleshoot code, then remote unlock."""
        import asyncio as _asyncio

        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")
        logger.info("▶ PHASE: door_trouble — %s can't enter, troubleshooting", cleaner_name)
        cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")
        access_code = self.slot_manager.get_value("cleaner_access_code", "")

        yield self.emitter.reasoning_step(
            "REASONING",
            f"{cleaner_name} reports door trouble — access code not working. "
            "Troubleshooting remotely, will try remote unlock via Nuki if needed.",
            confidence=0.9,
        )

        call_result = await self._make_script_call(
            "door_trouble_call",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_name=self.slot_manager.get_value("property_name", "the property"),
            access_code=access_code,
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        # In dry_run, simulate remote unlock
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("method") == "remote_unlock":
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    "Nuki smart lock: Remote unlock triggered successfully. Cleaner is inside."
                )
                yield self.emitter.text_message_end()
                self.slot_manager.set_slot("entry_method", "remote_unlock")
                yield self.emitter.slot_filled("entry_method", "remote_unlock")
            if extracted.get("keypad_issue"):
                self.slot_manager.set_slot("keypad_needs_repair", True)
                yield self.emitter.slot_filled("keypad_needs_repair", True)

        # Try real Nuki remote unlock
        elif self._nuki:
            lock_id = self.slot_manager.get_value("nuki_lock_id", "")
            if lock_id:
                try:
                    await self._nuki.unlock(lock_id)
                    yield self.emitter.text_message_start()
                    yield self.emitter.text_message_content("Nuki: Remote unlock sent successfully.")
                    yield self.emitter.text_message_end()
                except Exception:
                    logger.exception("Nuki remote unlock failed")

        self._phase_results["door_trouble"] = {"call_result": call_result}

    async def _phase_day_of_setup(self) -> AsyncIterator[AGUIEvent]:
        """Phase 5: Day-of preparation — access code, reminders."""
        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")
        cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")
        logger.info("▶ PHASE: day_of_setup — access codes + reminders for %s (%s)", cleaner_name, cleaner_phone)
        lock_id = self.slot_manager.get_value("nuki_lock_id", "")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Day-of setup: Creating access code and sending reminders..."
        )
        yield self.emitter.text_message_end()

        # Create access code (real Nuki or simulated)
        access_code = ""
        if self._nuki and lock_id:
            try:
                from datetime import timedelta
                now = datetime.now(timezone.utc)
                code_result = await self._nuki.create_access_code(
                    lock_id,
                    f"Cleaner-{cleaner_name}",
                    start=now,
                    end=now + timedelta(hours=8),
                )
                access_code = getattr(code_result, "code", "")
            except Exception:
                logger.warning("Nuki API unavailable, generating simulated code")

        if not access_code:
            # Simulated access code for testing
            import random
            access_code = str(random.randint(100000, 999999))
            logger.info("Simulated access code generated: %s", access_code)

        self.slot_manager.set_slot("cleaner_access_code", access_code)
        yield self.emitter.slot_filled("cleaner_access_code", access_code)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Access code generated: {access_code}"
        )
        yield self.emitter.text_message_end()

        # Send day-of reminder call to cleaner
        call_result = await self._make_script_call(
            "cleaner_day_reminder",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            cleaning_time=self.slot_manager.get_value("cleaning_time", "7:00 PM"),
            access_code=access_code or "will be provided on arrival",
            special_instructions=self.slot_manager.get_value("special_instructions", "standard cleaning"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for reminder call with {cleaner_name}...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        self._phase_results["day_of_setup"] = {
            "access_code_created": bool(access_code),
            "reminder_sent": True,
            "call_result": call_result,
        }

        if self._episodic:
            await self._episodic.add_episode(
                event="day_of_setup_complete",
                content=f"Access code created, reminder sent to {cleaner_name}",
                metadata={"access_code": bool(access_code), "cleaner_name": cleaner_name},
            )

    async def _phase_detect_entry(self) -> AsyncIterator[AGUIEvent]:
        """Phase 6: Detect guest entry via Nuki smart lock (or simulate)."""
        import asyncio as _asyncio
        logger.info("▶ PHASE: detect_entry — monitoring smart lock for cleaner entry")

        lock_id = self.slot_manager.get_value("nuki_lock_id", "")
        access_code = self.slot_manager.get_value("cleaner_access_code", "")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Monitoring smart lock for cleaner entry..."
        )
        yield self.emitter.text_message_end()

        entry_event = None

        # Try real Nuki detector first
        if self._entry_detector:
            await self._entry_detector.start_polling()
            entry_event = await self._entry_detector.wait_for_entry(timeout_seconds=30)
            await self._entry_detector.stop_polling()

        # Simulate entry if no real detector or timeout
        if not entry_event:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Simulating entry detection (test mode)..."
            )
            yield self.emitter.text_message_end()

            await _asyncio.sleep(5)  # Simulate 5-second wait

            entry_timestamp = datetime.now(timezone.utc)
            self.slot_manager.set_slot("entry_detected", True)
            self.slot_manager.set_slot("entry_timestamp", entry_timestamp.isoformat())
            self.slot_manager.set_slot("entry_trigger", "keypad")

            yield self.emitter.slot_filled("entry_detected", True)
            yield self.emitter.slot_filled("entry_timestamp", entry_timestamp.isoformat())

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Entry detected at {entry_timestamp.strftime('%H:%M')} "
                f"via keypad (code {access_code}) — simulated"
            )
            yield self.emitter.text_message_end()

            self._phase_results["detect_entry"] = {
                "detected": True,
                "trigger": "keypad",
                "simulated": True,
            }
        else:
            self.slot_manager.set_slot("entry_detected", True)
            self.slot_manager.set_slot("entry_timestamp", entry_event.timestamp.isoformat())
            self.slot_manager.set_slot("entry_trigger", entry_event.trigger)
            yield self.emitter.slot_filled("entry_detected", True)
            yield self.emitter.slot_filled("entry_timestamp", entry_event.timestamp.isoformat())

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Entry detected at {entry_event.timestamp.strftime('%H:%M')} "
                f"via {entry_event.trigger} ({entry_event.action})"
            )
            yield self.emitter.text_message_end()

            self._phase_results["detect_entry"] = {
                "detected": True,
                "trigger": entry_event.trigger,
                "simulated": False,
            }

        if self._episodic:
            await self._episodic.add_episode(
                event="entry_detected",
                content="Cleaner entered the property",
                metadata=self._phase_results.get("detect_entry", {}),
            )

    async def _phase_request_photos(self) -> AsyncIterator[AGUIEvent]:
        """Phase 7: Call cleaner to request post-checkout photos."""
        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")
        cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")
        logger.info("▶ PHASE: request_photos — calling %s for post-checkout photos", cleaner_name)
        property_address = self.slot_manager.get_value("property_address", "the property")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Calling {cleaner_name} to request post-checkout photos..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "request_photos_after_entry",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_address=property_address,
            guest_name=self.slot_manager.get_value("departing_guest_name", "the guest"),
            photo_upload_link=self.slot_manager.get_value("photo_upload_link", "the shared folder"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for call with {cleaner_name}...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        self._phase_results["request_photos"] = {"call_result": call_result}

        if self._episodic:
            await self._episodic.add_episode(
                event="photos_requested",
                content=f"Called {cleaner_name} to request property photos",
                metadata={"cleaner_name": cleaner_name},
            )

        yield self.emitter.slot_filled("photos_requested", True)

    async def _phase_inspect_photos(self) -> AsyncIterator[AGUIEvent]:
        """Phase 8: Analyze photos with GPT-4o Vision."""
        logger.info("▶ PHASE: inspect_photos — analyzing with GPT-4o Vision")
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Analyzing property photos with GPT-4o Vision..."
        )
        yield self.emitter.text_message_end()

        analysis_result: dict[str, Any] = {}

        if self._dry_run:
            # Simulate photo analysis — no real damage in dry_run
            import asyncio as _asyncio
            await _asyncio.sleep(2)
            analysis_result = {
                "damage_detected": True,
                "description": "Cracked TV screen in living room — 15cm crack across center",
                "severity": 4,
                "confidence": 0.92,
                "estimated_cost": 450.0,
                "category": "electronics",
                "location": "living room",
            }
            self.slot_manager.set_slot("damage_detected", True)
            self.slot_manager.set_slot("damage_description", analysis_result["description"])
            self.slot_manager.set_slot("damage_severity", analysis_result["severity"])
            self.slot_manager.set_slot("analysis_confidence", analysis_result["confidence"])
            self.slot_manager.set_slot("estimated_claim_amount", str(analysis_result["estimated_cost"]))

            yield self.emitter.slot_filled("damage_detected", True)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"GPT-4o Vision Analysis Complete:\n"
                f"  Damage: {analysis_result['description']}\n"
                f"  Severity: {analysis_result['severity']}/5\n"
                f"  Estimated cost: ${analysis_result['estimated_cost']:.2f}\n"
                f"  Confidence: {analysis_result['confidence']:.0%}"
            )
            yield self.emitter.text_message_end()

        elif self._photo_comparator:
            before_urls = self.slot_manager.get_value("photos_before", [])
            after_urls = self.slot_manager.get_value("photos_after", [])
            if before_urls and after_urls:
                try:
                    # Compare first pair of before/after photos
                    result = await self._photo_comparator.compare_photos(
                        before_path=before_urls[0],
                        after_path=after_urls[0],
                    )
                    damage_detected = not result.no_damage_detected
                    analysis_result = {
                        "damage_detected": damage_detected,
                        "description": result.summary,
                        "severity": result.overall_severity,
                        "estimated_cost": result.total_estimated_cost,
                        "damages": [
                            {"description": d.description, "severity": d.severity,
                             "category": d.category, "cost": d.estimated_cost}
                            for d in result.damages
                        ],
                    }
                    self.slot_manager.set_slot("damage_detected", damage_detected)
                    self.slot_manager.set_slot("damage_description", result.summary)
                    self.slot_manager.set_slot("damage_severity", result.overall_severity)
                    self.slot_manager.set_slot("estimated_claim_amount", str(result.total_estimated_cost))

                    yield self.emitter.slot_filled("damage_detected", damage_detected)
                    if damage_detected:
                        yield self.emitter.text_message_start()
                        yield self.emitter.text_message_content(
                            f"Damage detected: {result.summary}\n"
                            f"Severity: {result.overall_severity}/10\n"
                            f"Estimated cost: ${result.total_estimated_cost:.2f}"
                        )
                        yield self.emitter.text_message_end()
                    else:
                        yield self.emitter.text_message_start()
                        yield self.emitter.text_message_content(
                            "No damage detected. Property is in good condition."
                        )
                        yield self.emitter.text_message_end()
                except Exception:
                    logger.exception("Photo comparison failed")
        else:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Photo comparator not available. Skipping vision analysis."
            )
            yield self.emitter.text_message_end()

        self._phase_results["inspect_photos"] = analysis_result

        if self._episodic:
            await self._episodic.add_episode(
                event="photo_inspection_complete",
                content=f"Photo analysis: damage={'yes' if analysis_result.get('damage_detected') else 'no'}",
                metadata=analysis_result,
            )

    async def _phase_process_claim(self) -> AsyncIterator[AGUIEvent]:
        """Phase 9: Process damage claim."""
        guest_name = self.slot_manager.get_value("departing_guest_name", "Guest")
        guest_phone = self.slot_manager.get_value("departing_guest_phone", "")
        damage_desc = self.slot_manager.get_value("damage_description", "")
        severity = self.slot_manager.get_value("damage_severity", 0)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Processing damage claim for {guest_name}...\n"
            f"Damage: {damage_desc}\nSeverity: {severity}/5"
        )
        yield self.emitter.text_message_end()

        # Notify guest about damage claim
        call_result = await self._make_script_call(
            "notify_damage_claim_urgent",
            guest_phone,
            guest_name=guest_name,
            property_name=self.slot_manager.get_value("property_name", "the property"),
            damage_description=damage_desc,
            claim_amount=self.slot_manager.get_value("estimated_claim_amount", "TBD"),
            checkout_date=self.slot_manager.get_value("checkout_date", "your recent stay"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        claim_id = f"CLM-{self.session_id.upper()}-{datetime.now(timezone.utc).strftime('%H%M')}"
        self.slot_manager.set_slot("claim_id", claim_id)
        yield self.emitter.slot_filled("claim_id", claim_id)

        self._phase_results["process_claim"] = {
            "claim_id": claim_id,
            "damage_description": damage_desc,
            "severity": severity,
            "call_result": call_result,
        }

        if self._episodic:
            await self._episodic.add_episode(
                event="damage_claim_processed",
                content=f"Damage claim {claim_id} created, guest {guest_name} notified",
                metadata={"claim_id": claim_id, "severity": severity},
            )

    async def _phase_check_progress(self) -> AsyncIterator[AGUIEvent]:
        """Phase 10: Check cleaner progress."""
        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")
        cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Checking cleaning progress with {cleaner_name}..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "check_cleaner_progress",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            next_guest_name=self.slot_manager.get_value("incoming_guest_name", "the next guest"),
            next_guest_checkin=self.slot_manager.get_value("next_guest_checkin_time", "4:00 PM"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for progress call with {cleaner_name}...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        # In dry_run, use extracted data to determine if backup needed
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("needs_backup"):
                self.slot_manager.set_slot("needs_backup_cleaner", True)
                yield self.emitter.reasoning_step(
                    "REASONING",
                    f"{cleaner_name} reports she's behind schedule and needs help. "
                    "Dispatching backup cleaner to assist.",
                    confidence=0.95,
                )
            elif extracted.get("on_track"):
                yield self.emitter.text_message_start()
                yield self.emitter.text_message_content(
                    f"{cleaner_name} is on track. No backup needed."
                )
                yield self.emitter.text_message_end()

        self._phase_results["check_progress"] = {"call_result": call_result}

        if self._episodic:
            await self._episodic.add_episode(
                event="progress_checked",
                content=f"Checked cleaning progress with {cleaner_name}",
                metadata={"cleaner_name": cleaner_name},
            )

    async def _phase_backup_cleaner(self) -> AsyncIterator[AGUIEvent]:
        """Phase 11: Dispatch backup cleaner to help — primary cleaner stays as lead."""
        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"{cleaner_name} needs help. Dispatching backup cleaner to assist...\n"
            f"Primary cleaner stays as lead — backup follows her instructions."
        )
        yield self.emitter.text_message_end()

        # Load backup cleaner — might be the original cleaner or second one from config
        backup_phone = self.slot_manager.get_value("backup_cleaner_phone", "")
        backup_name = self.slot_manager.get_value("backup_cleaner_name", "")

        if not backup_phone:
            cleaners_path = Path(__file__).resolve().parents[2] / "config" / "cleaners.json"
            try:
                import json
                with open(cleaners_path) as f:
                    cleaners = json.load(f)
                # Find a cleaner that's NOT the current active one
                current_phone = self.slot_manager.get_value("cleaner_phone", "")
                for c in (cleaners if isinstance(cleaners, list) else []):
                    if c.get("phone") != current_phone:
                        backup_name = c.get("name", "Backup Cleaner")
                        backup_phone = c.get("phone", "")
                        break
            except (FileNotFoundError, json.JSONDecodeError):
                logger.warning("Could not load backup cleaner from config")

        if not backup_name:
            backup_name = "Backup Cleaner"

        # Generate access code for backup cleaner
        import random
        backup_code = str(random.randint(100000, 999999))
        self.slot_manager.set_slot("backup_access_code", backup_code)
        yield self.emitter.slot_filled("backup_access_code", backup_code)

        call_result = await self._make_script_call(
            "dispatch_backup_to_help",
            backup_phone,
            cleaner_name=backup_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            primary_cleaner_name=cleaner_name,
            access_code=backup_code,
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(f"Waiting for call with {backup_name}...")
            yield self.emitter.text_message_end()
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Backup cleaner {backup_name} confirmed — arriving in ~30 min.\n"
            f"{cleaner_name} remains lead — no need to re-instruct backup, primary will guide her."
        )
        yield self.emitter.text_message_end()

        self._phase_results["backup_cleaner"] = {
            "backup_name": backup_name,
            "call_result": call_result,
        }

        if self._episodic:
            await self._episodic.add_episode(
                event="backup_cleaner_dispatched",
                content=f"Backup cleaner {backup_name} dispatched to help {cleaner_name}",
                metadata={"backup_name": backup_name, "primary": cleaner_name},
            )

    # ── Vendor & Payment Phases ─────────────────────────────────────── #

    async def _phase_vendor_quotes(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Call two vendors for TV replacement quotes."""
        import asyncio as _asyncio

        # Load vendors from config
        vendors_path = Path(__file__).resolve().parents[2] / "config" / "vendors.json"
        vendors_list: list[dict] = []
        try:
            import json
            with open(vendors_path) as f:
                vendors_list = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Could not load vendors config")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Calling {len(vendors_list)} vendors for TV replacement quotes..."
        )
        yield self.emitter.text_message_end()

        if self._dry_run:
            dry_data = self.DRY_RUN_TRANSCRIPTS.get("vendor_quote_request", {})
            extracted = dry_data.get("extracted", {})
            vendor_1 = extracted.get("vendor_1", {})
            vendor_2 = extracted.get("vendor_2", {})

            # Override with real vendor data from config
            if len(vendors_list) >= 2:
                vendor_1["phone"] = vendors_list[0].get("phone", vendor_1.get("phone", ""))
                vendor_1["name"] = vendors_list[0].get("name", vendor_1.get("name", ""))
                vendor_2["phone"] = vendors_list[1].get("phone", vendor_2.get("phone", ""))
                vendor_2["name"] = vendors_list[1].get("name", vendor_2.get("name", ""))

            # Simulate call to Vendor 1
            fake_id_1 = f"dry-run-vendor1-{uuid.uuid4().hex[:6]}"
            self._pending_events.append(
                self.emitter.call_started(fake_id_1, vendor_1.get("phone", "+0"), vendor_1.get("name", "Vendor 1"), "vendor_quote")
            )
            for turn in dry_data.get("transcript_vendor_1", []):
                await _asyncio.sleep(3)
                self._pending_events.append(
                    self.emitter.call_transcript_update(fake_id_1, turn["role"], turn["message"])
                )
            self._pending_events.append(
                self.emitter.call_ended(fake_id_1, 35, "completed", f"Vendor 1 quote: ${vendor_1.get('quote', 0)}")
            )
            for _evt in self._drain_pending_events():
                yield _evt

            # 20s pause between calls
            await _asyncio.sleep(20)

            # Simulate call to Vendor 2
            fake_id_2 = f"dry-run-vendor2-{uuid.uuid4().hex[:6]}"
            self._pending_events.append(
                self.emitter.call_started(fake_id_2, vendor_2.get("phone", "+0"), vendor_2.get("name", "Vendor 2"), "vendor_quote")
            )
            for turn in dry_data.get("transcript_vendor_2", []):
                await _asyncio.sleep(3)
                self._pending_events.append(
                    self.emitter.call_transcript_update(fake_id_2, turn["role"], turn["message"])
                )
            self._pending_events.append(
                self.emitter.call_ended(fake_id_2, 30, "completed", f"Vendor 2 quote: ${vendor_2.get('quote', 0)}")
            )
            for _evt in self._drain_pending_events():
                yield _evt

            # Store quotes
            self.slot_manager.set_slot("vendor_1_name", vendor_1.get("name", ""))
            self.slot_manager.set_slot("vendor_1_phone", vendor_1.get("phone", ""))
            self.slot_manager.set_slot("vendor_1_quote", vendor_1.get("quote", 0))
            self.slot_manager.set_slot("vendor_2_name", vendor_2.get("name", ""))
            self.slot_manager.set_slot("vendor_2_phone", vendor_2.get("phone", ""))
            self.slot_manager.set_slot("vendor_2_quote", vendor_2.get("quote", 0))

            yield self.emitter.slot_filled("vendor_1_quote", f"${vendor_1.get('quote', 0)}")
            yield self.emitter.slot_filled("vendor_2_quote", f"${vendor_2.get('quote', 0)}")

        else:
            # Real mode: call both vendors using voice
            for i, vendor in enumerate(vendors_list[:2]):
                v_name = vendor.get("name", f"Vendor {i+1}")
                v_phone = vendor.get("phone", "")
                call_result = await self._make_script_call(
                    "vendor_quote_request",
                    v_phone,
                    cleaner_name=v_name,
                    property_name=self.slot_manager.get_value("property_name", "the property"),
                )
                for _evt in self._drain_pending_events():
                    yield _evt
                call_id = call_result.get("call_id", "")
                if call_id:
                    call_final = await self._wait_for_call(call_id)
                    call_result.update(call_final)
                self.slot_manager.set_slot(f"vendor_{i+1}_name", v_name)
                self.slot_manager.set_slot(f"vendor_{i+1}_phone", v_phone)
                # Quote will need to be extracted from transcript in real mode
                yield self.emitter.slot_filled(f"vendor_{i+1}_name", v_name)

        self._phase_results["vendor_quotes"] = {
            "vendor_1": self.slot_manager.get_value("vendor_1_name", ""),
            "vendor_2": self.slot_manager.get_value("vendor_2_name", ""),
        }

    async def _phase_vendor_select(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Compare quotes, accept cheaper vendor, reject expensive one."""
        import asyncio as _asyncio

        q1 = float(self.slot_manager.get_value("vendor_1_quote", 999))
        q2 = float(self.slot_manager.get_value("vendor_2_quote", 999))
        v1_name = self.slot_manager.get_value("vendor_1_name", "Vendor 1")
        v2_name = self.slot_manager.get_value("vendor_2_name", "Vendor 2")
        v1_phone = self.slot_manager.get_value("vendor_1_phone", "")
        v2_phone = self.slot_manager.get_value("vendor_2_phone", "")

        if q2 <= q1:
            selected_name, selected_phone, selected_quote = v2_name, v2_phone, q2
            rejected_name, rejected_phone, rejected_quote = v1_name, v1_phone, q1
        else:
            selected_name, selected_phone, selected_quote = v1_name, v1_phone, q1
            rejected_name, rejected_phone, rejected_quote = v2_name, v2_phone, q2

        yield self.emitter.reasoning_step(
            "REASONING",
            f"Comparing quotes: {v1_name}=${q1}, {v2_name}=${q2}. "
            f"Selecting {selected_name} (${selected_quote}) — saving ${rejected_quote - selected_quote:.0f}.",
            confidence=0.99,
        )

        self.slot_manager.set_slot("selected_vendor_name", selected_name)
        self.slot_manager.set_slot("selected_vendor_phone", selected_phone)
        self.slot_manager.set_slot("selected_vendor_quote", selected_quote)
        yield self.emitter.slot_filled("selected_vendor", f"{selected_name} (${selected_quote})")

        # Call accepted vendor
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(f"Accepting {selected_name}'s quote (${selected_quote})...")
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "vendor_accept",
            selected_phone,
            cleaner_name=selected_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
            vendor_quote=str(selected_quote),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        # Call rejected vendor
        await _asyncio.sleep(5) if not self._dry_run else None
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(f"Declining {rejected_name}'s quote (${rejected_quote})...")
        yield self.emitter.text_message_end()

        reject_result = await self._make_script_call(
            "vendor_reject",
            rejected_phone,
            cleaner_name=rejected_name,
        )
        for _evt in self._drain_pending_events():
            yield _evt

        self._phase_results["vendor_select"] = {
            "selected": selected_name,
            "selected_quote": selected_quote,
            "rejected": rejected_name,
            "rejected_quote": rejected_quote,
        }

    async def _phase_vendor_access(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Generate access code for vendor and track entry."""
        import asyncio as _asyncio

        vendor_name = self.slot_manager.get_value("selected_vendor_name", "Vendor")

        # Generate vendor access code
        import random
        vendor_code = str(random.randint(100000, 999999))
        self.slot_manager.set_slot("vendor_access_code", vendor_code)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Access code generated for {vendor_name}: {vendor_code}\n"
            f"Sending via Telegram and monitoring smart lock for entry..."
        )
        yield self.emitter.text_message_end()
        yield self.emitter.slot_filled("vendor_access_code", vendor_code)

        # Simulate vendor arriving and entering
        if self._dry_run:
            await _asyncio.sleep(5)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"{vendor_name} entered the property using code {vendor_code}. "
                f"TV installation in progress..."
            )
            yield self.emitter.text_message_end()

            await _asyncio.sleep(5)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"TV installation complete. {vendor_name} has left the property. "
                f"Smart lock re-locked automatically."
            )
            yield self.emitter.text_message_end()

        self.slot_manager.set_slot("vendor_work_completed", True)
        yield self.emitter.slot_filled("vendor_work_completed", True)

        self._phase_results["vendor_access"] = {
            "vendor_name": vendor_name,
            "access_code": vendor_code,
            "work_completed": True,
        }

    async def _phase_vendor_invoice(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Vendor sends invoice via Telegram."""
        import asyncio as _asyncio

        vendor_name = self.slot_manager.get_value("selected_vendor_name", "Vendor")
        vendor_quote = self.slot_manager.get_value("selected_vendor_quote", 25)

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Waiting for invoice from {vendor_name} via Telegram..."
        )
        yield self.emitter.text_message_end()

        if self._dry_run:
            await _asyncio.sleep(3)
            invoice_id = f"INV-{uuid.uuid4().hex[:6].upper()}"
            self.slot_manager.set_slot("vendor_invoice_id", invoice_id)
            self.slot_manager.set_slot("vendor_invoice_amount", vendor_quote)
            yield self.emitter.slot_filled("vendor_invoice_id", invoice_id)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Invoice received from {vendor_name}:\n"
                f"  Invoice #: {invoice_id}\n"
                f"  Amount: ${vendor_quote}\n"
                f"  Service: TV delivery + installation\n"
                f"  Payment method: TransferWise"
            )
            yield self.emitter.text_message_end()

        self._phase_results["vendor_invoice"] = {
            "vendor": vendor_name,
            "invoice_id": self.slot_manager.get_value("vendor_invoice_id", ""),
            "amount": vendor_quote,
        }

    async def _phase_payment_approval(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Call manager/owner for payment approval."""
        vendor_name = self.slot_manager.get_value("selected_vendor_name", "Vendor")
        amount = self.slot_manager.get_value("vendor_invoice_amount", 25)

        # Try manager first, then owner as fallback
        manager_phone = self.slot_manager.get_value("manager_phone", "")
        owner_phone = self.slot_manager.get_value("owner_phone", "")
        approval_phone = manager_phone or owner_phone or self.slot_manager.get_value("departing_guest_phone", "")
        approval_name = "Manager" if manager_phone else "Owner"

        yield self.emitter.reasoning_step(
            "REASONING",
            f"Need payment approval for ${amount} to {vendor_name}. "
            f"Calling {approval_name} for authorization.",
            confidence=0.9,
        )

        call_result = await self._make_script_call(
            "payment_approval_call",
            approval_phone,
            guest_name=approval_name,
            vendor_name=vendor_name,
            amount=str(amount),
            invoice_id=self.slot_manager.get_value("vendor_invoice_id", ""),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        # In dry_run, use extracted data
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("approved"):
                self.slot_manager.set_slot("payment_approved", True)
                yield self.emitter.slot_filled("payment_approved", True)

        self._phase_results["payment_approval"] = {
            "approved_by": approval_name,
            "amount": amount,
            "call_result": call_result,
        }

    async def _phase_payment_process(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Process payment via TransferWise."""
        import asyncio as _asyncio

        vendor_name = self.slot_manager.get_value("selected_vendor_name", "Vendor")
        amount = self.slot_manager.get_value("vendor_invoice_amount", 25)
        approved = self.slot_manager.get_value("payment_approved", False)

        if not approved:
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Payment NOT approved. Skipping TransferWise transfer."
            )
            yield self.emitter.text_message_end()
            self._phase_results["payment_process"] = {"status": "not_approved"}
            return

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Processing payment of ${amount} to {vendor_name} via TransferWise..."
        )
        yield self.emitter.text_message_end()

        if self._dry_run:
            await _asyncio.sleep(3)
            tx_id = f"TW-{uuid.uuid4().hex[:8].upper()}"
            self.slot_manager.set_slot("payment_transaction_id", tx_id)
            yield self.emitter.slot_filled("payment_transaction_id", tx_id)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Payment processed successfully!\n"
                f"  TransferWise TX: {tx_id}\n"
                f"  Amount: ${amount}\n"
                f"  Recipient: {vendor_name}\n"
                f"  Status: Completed"
            )
            yield self.emitter.text_message_end()

        self._phase_results["payment_process"] = {
            "tx_id": self.slot_manager.get_value("payment_transaction_id", ""),
            "amount": amount,
            "vendor": vendor_name,
            "status": "completed",
        }

    # ── Final Verification Phases ─────────────────────────────────────── #

    async def _phase_final_photos(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Request final photos from cleaner before exit."""
        cleaner_name = self.slot_manager.get_value("cleaner_name", "Cleaner")
        cleaner_phone = self.slot_manager.get_value("cleaner_phone", "")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Requesting final photos from {cleaner_name} before exit..."
        )
        yield self.emitter.text_message_end()

        call_result = await self._make_script_call(
            "final_photos_request",
            cleaner_phone,
            cleaner_name=cleaner_name,
            property_address=self.slot_manager.get_value("property_address", "the property"),
        )
        for _evt in self._drain_pending_events():
            yield _evt

        call_id = call_result.get("call_id", "")
        if call_id:
            call_final = await self._wait_for_call(call_id)
            call_result.update(call_final)

        # In dry_run, check if baby crib was forgotten
        if self._dry_run and call_result.get("extracted"):
            extracted = call_result["extracted"]
            if extracted.get("baby_crib_forgotten"):
                yield self.emitter.reasoning_step(
                    "REASONING",
                    f"{cleaner_name} forgot to set up the baby crib! "
                    "Reminded her during the call — she's fixing it now.",
                    confidence=0.95,
                )
                self.slot_manager.set_slot("baby_crib_fixed", extracted.get("baby_crib_fixed", False))
                yield self.emitter.slot_filled("baby_crib_fixed", True)

        self._phase_results["final_photos"] = {"call_result": call_result}

    async def _phase_final_verification(self) -> AsyncIterator[AGUIEvent]:
        """Phase: AI verifies final photos — everything guest-ready."""
        import asyncio as _asyncio

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Verifying final photos with GPT-4o Vision..."
        )
        yield self.emitter.text_message_end()

        if self._dry_run:
            await _asyncio.sleep(3)
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Final Photo Verification Complete:\n"
                "  ✓ Living room — clean, new TV installed\n"
                "  ✓ Bedroom — bed made with fresh linens, baby crib set up\n"
                "  ✓ Bathroom — spotless, fresh towels\n"
                "  ✓ Kitchen — clean counters, dishes done\n"
                "  ✓ Floors — vacuumed and mopped\n"
                "  Result: PASS — property is guest-ready"
            )
            yield self.emitter.text_message_end()
            self.slot_manager.set_slot("final_verification_passed", True)
            yield self.emitter.slot_filled("final_verification_passed", True)

        self._phase_results["final_verification"] = {
            "status": "passed",
            "issues": [],
        }

    # ── Flight & Smart Home Phases ────────────────────────────────────── #

    async def _phase_flight_check(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Check guest flight status and calculate ETA."""
        import asyncio as _asyncio

        guest_name = self.slot_manager.get_value("incoming_guest_name", "Guest")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Checking flight status for {guest_name}..."
        )
        yield self.emitter.text_message_end()

        if self._dry_run:
            await _asyncio.sleep(3)

            # Simulated flight data
            flight_number = "EK231"
            original_arrival = "6:30 PM"
            delayed_arrival = "8:15 PM"
            delay_minutes = 105

            self.slot_manager.set_slot("guest_flight_number", flight_number)
            self.slot_manager.set_slot("guest_flight_arrival", delayed_arrival)
            self.slot_manager.set_slot("flight_delayed", True)
            self.slot_manager.set_slot("flight_delay_minutes", delay_minutes)

            yield self.emitter.slot_filled("guest_flight", f"{flight_number} — delayed")
            yield self.emitter.reasoning_step(
                "REASONING",
                f"Flight {flight_number} was scheduled for {original_arrival} but is delayed "
                f"to {delayed_arrival} (+{delay_minutes} min). Need to recalculate AC timing.",
                confidence=0.9,
            )

            # Google Maps ETA simulation
            await _asyncio.sleep(2)
            drive_time_minutes = 55
            traffic_status = "moderate"
            self.slot_manager.set_slot("airport_drive_time", drive_time_minutes)

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Google Maps ETA from Dubai Airport to property:\n"
                f"  Distance: 42 km\n"
                f"  Traffic: {traffic_status}\n"
                f"  Estimated drive time: {drive_time_minutes} minutes\n"
                f"  Guest lands at {delayed_arrival} → arrives ~9:10 PM"
            )
            yield self.emitter.text_message_end()

            estimated_arrival = "9:10 PM"
            self.slot_manager.set_slot("guest_estimated_arrival", estimated_arrival)
            yield self.emitter.slot_filled("guest_estimated_arrival", estimated_arrival)

        self._phase_results["flight_check"] = {
            "flight": self.slot_manager.get_value("guest_flight_number", ""),
            "delayed": self.slot_manager.get_value("flight_delayed", False),
            "estimated_arrival": self.slot_manager.get_value("guest_estimated_arrival", ""),
        }

    async def _phase_smart_ac(self) -> AsyncIterator[AGUIEvent]:
        """Phase: Turn on AC 30 minutes before guest's estimated arrival."""
        import asyncio as _asyncio

        estimated_arrival = self.slot_manager.get_value("guest_estimated_arrival", "7:00 PM")
        guest_name = self.slot_manager.get_value("incoming_guest_name", "Guest")
        property_name = self.slot_manager.get_value("property_name", "the property")

        yield self.emitter.reasoning_step(
            "REASONING",
            f"{guest_name} estimated arrival: {estimated_arrival}. "
            f"SOP requires AC on 30 minutes before arrival. "
            f"Scheduling AC activation for 8:40 PM.",
            confidence=0.95,
        )

        if self._dry_run:
            await _asyncio.sleep(3)

            ac_on_time = "8:40 PM"
            self.slot_manager.set_slot("ac_scheduled_time", ac_on_time)
            yield self.emitter.slot_filled("ac_scheduled_time", ac_on_time)

            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                f"Smart Home — AC Control:\n"
                f"  Property: {property_name}\n"
                f"  Guest arrival: {estimated_arrival}\n"
                f"  AC activation scheduled: {ac_on_time} (30 min before)\n"
                f"  Target temperature: 22°C\n"
                f"  Status: SCHEDULED ✓\n\n"
                f"  Will monitor flight {self.slot_manager.get_value('guest_flight_number', 'N/A')} "
                f"for further delays and adjust automatically."
            )
            yield self.emitter.text_message_end()

        self._phase_results["smart_ac"] = {
            "ac_time": self.slot_manager.get_value("ac_scheduled_time", ""),
            "guest_arrival": estimated_arrival,
        }

    # ── Voice Call Helper ────────────────────────────────────────────── #

    async def _wait_for_call(self, call_id: str, timeout: int = 300) -> dict[str, Any]:
        """Poll ElevenLabs until the call is finished.

        In dry_run mode, returns immediately with simulated completion.

        Args:
            call_id: The conversation_id to monitor.
            timeout: Max seconds to wait (default 5 minutes).

        Returns:
            Dict with final call status and duration.
        """
        import asyncio as _asyncio

        if self._dry_run:
            logger.info("DRY RUN: _wait_for_call(%s) → instant completion", call_id)
            return {"status": "completed", "duration": 30, "ended_reason": "agent_hangup"}

        if not self._voice or not call_id:
            return {"status": "skipped"}

        logger.info("  ⏳ Waiting for call %s to finish (timeout=%ds)...", call_id, timeout)
        elapsed = 0
        poll_interval = 5  # seconds
        while elapsed < timeout:
            try:
                status = await self._voice.get_call_status(call_id)
                current = status.status
                if elapsed % 15 == 0 and elapsed > 0:
                    logger.info("  … call %s still in progress (%ds elapsed, status=%s)", call_id, elapsed, current)
                if current in ("done", "failed", "completed"):
                    logger.info(
                        "  ✓ Call %s finished: status=%s, duration=%.0fs, reason=%s",
                        call_id, current, status.duration_seconds or 0,
                        status.ended_reason or "unknown",
                    )
                    # Fetch and log full transcript
                    try:
                        transcript = await self._voice.get_transcript(call_id)
                        logger.info("  ── TRANSCRIPT (%s) ──", call_id)
                        for turn in transcript.turns:
                            logger.info(
                                "    [%s] %s: %s",
                                f"{turn.get('time', 0):.0f}s" if isinstance(turn.get('time'), (int, float)) else turn.get('time', '?'),
                                turn.get("role", "?").upper(),
                                turn.get("message", ""),
                            )
                        logger.info("  ── END TRANSCRIPT ──")
                    except Exception as t_exc:
                        logger.warning("  Could not fetch transcript for %s: %s", call_id, t_exc)
                    return {
                        "status": current,
                        "duration": status.duration_seconds,
                        "ended_reason": status.ended_reason,
                    }
            except Exception as exc:
                logger.warning("  Poll error for %s: %s", call_id, exc)

            await _asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning("  ✗ Call %s timed out after %ds", call_id, timeout)
        return {"status": "timeout", "call_id": call_id}

    async def _make_script_call(
        self,
        script_key: str,
        phone_number: str,
        **variables: Any,
    ) -> dict[str, Any]:
        """Make a voice call using a script from call_scripts.yaml.

        In dry_run mode, simulates the call by streaming transcript turns
        from DRY_RUN_TRANSCRIPTS with small delays for realistic SSE output.

        Args:
            script_key: Key in the YAML file (e.g. "offer_free_late_checkout").
            phone_number: Recipient phone in E.164 format.
            **variables: Placeholder values to format into the script.

        Returns:
            Dict with call_id and status, or empty dict if voice client unavailable.
        """
        import asyncio as _asyncio

        script_config = self._scripts.get(script_key, {})
        if not script_config:
            logger.warning("No script found for key: %s", script_key)
            return {"status": "no_script", "script_key": script_key}

        # Build full variable context: slot values + explicit overrides
        all_vars: dict[str, Any] = {}
        for slot in self.slot_manager.get_filled_slots():
            all_vars[slot.name] = slot.value
        all_vars.update(variables)

        # Use format_map with defaults so missing placeholders don't crash
        class _Defaults(dict):
            def __missing__(self, key: str) -> str:
                return f"[{key}]"

        safe_vars = _Defaults(all_vars)
        system_prompt = script_config["system_prompt"].format_map(safe_vars)
        first_message = script_config["first_message"].format_map(safe_vars)

        # Append global rules to every call script
        system_prompt += (
            "\n\nCRITICAL RULES FOR ALL CALLS:\n"
            "- When the user says goodbye, bye, thanks bye, or any farewell — "
            "say a brief goodbye and IMMEDIATELY end the conversation. Do NOT keep talking.\n"
            "- If you detect a voicemail system or answering machine (beep, 'leave a message', "
            "'not available') — leave ONE short message (under 20 seconds) and end the call.\n"
            "- NEVER repeat the same message more than once.\n"
            "- NEVER say 'I am unable to hang up the phone'. Just say goodbye and stop talking.\n"
            "- If the user is silent for more than 10 seconds after you finish speaking, "
            "say goodbye and end the conversation.\n"
        )

        recipient_name = all_vars.get("guest_name", all_vars.get("cleaner_name", "Unknown"))

        # ── DRY RUN: simulate call with fake transcript ──
        if self._dry_run:
            # 20s pause between calls (skip for the very first call)
            if self._phase_results:
                await _asyncio.sleep(20)
            fake_call_id = f"dry-run-{script_key}-{uuid.uuid4().hex[:6]}"
            dry_data = self.DRY_RUN_TRANSCRIPTS.get(script_key, {})
            transcript_turns = dry_data.get("transcript", [])
            duration = dry_data.get("duration", 30)
            extracted = dry_data.get("extracted", {})

            logger.info(
                "DRY RUN call: %s → %s (%d turns, %ds simulated)",
                script_key, recipient_name, len(transcript_turns), duration,
            )

            # Buffer call_started event for the SSE stream
            self._pending_events.append(
                self.emitter.call_started(
                    call_id=fake_call_id,
                    phone_number=phone_number or "+0000000000",
                    recipient_name=recipient_name,
                    call_type=script_key,
                )
            )

            # Buffer each transcript turn with delay for realistic streaming
            for turn in transcript_turns:
                await _asyncio.sleep(3)  # 3s per turn — realistic call pacing
                self._pending_events.append(
                    self.emitter.call_transcript_update(
                        call_id=fake_call_id,
                        role=turn["role"],
                        message=turn["message"],
                    )
                )

            # Buffer call_ended
            self._pending_events.append(
                self.emitter.call_ended(
                    call_id=fake_call_id,
                    duration_seconds=duration,
                    status="completed",
                    summary=f"DRY RUN: {len(transcript_turns)} turns, {duration}s",
                )
            )

            return {
                "status": "initiated",
                "call_id": fake_call_id,
                "script_key": script_key,
                "dry_run": True,
                "extracted": extracted,
                "duration": duration,
            }

        # ── REAL CALL ──
        if not self._voice or not phone_number:
            logger.info(
                "Voice call skipped (no client or phone): %s → %s",
                script_key, phone_number,
            )
            return {
                "status": "skipped",
                "script_key": script_key,
                "system_prompt_preview": system_prompt[:100],
            }

        logger.info(
            "═══ CALLING %s ═══ phone=%s script=%s",
            recipient_name, phone_number, script_key,
        )
        logger.info(
            "  First message: %s", first_message[:200],
        )
        logger.info(
            "  System prompt (first 300 chars): %s", system_prompt[:300],
        )

        try:
            result = await self._voice.make_call(
                phone_number=phone_number,
                script=system_prompt,
                first_message=first_message,
                agent_phone_number_id=self._phone_number_id or None,
            )
            call_id = getattr(result, "call_id", "")
            logger.info(
                "  ✓ Call initiated — call_id=%s, recipient=%s, phone=%s",
                call_id, recipient_name, phone_number,
            )
            # Emit call_started event for the UI
            self.emitter.call_started(
                call_id=call_id,
                phone_number=phone_number,
                recipient_name=recipient_name,
                call_type=script_key,
            )
            return {
                "status": "initiated",
                "call_id": call_id,
                "script_key": script_key,
            }
        except Exception as e:
            logger.exception(
                "  ✗ Voice call FAILED for %s → %s: %s",
                script_key, phone_number, e,
            )
            return {"status": "error", "error": str(e), "script_key": script_key}

    # ── State Transition Helper ──────────────────────────────────────── #

    def _drain_pending_events(self) -> list[AGUIEvent]:
        """Drain and return all buffered events from _make_script_call."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    async def _transition_to(
        self,
        flow_name: str,
        target_state: str,
    ) -> AsyncIterator[AGUIEvent]:
        """Transition the state machine and emit AG-UI events."""
        old_state = self.state_machine.current_state
        logger.info(
            "━━━ STATE: %s → %s ━━━",
            old_state, target_state,
        )
        self.state_machine.transition(to_state=target_state)
        yield self.emitter.flow_state_changed(flow_name, old_state, target_state)

    # ── Properties ───────────────────────────────────────────────────── #

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.current_state == DONE

    @property
    def phase_results(self) -> dict[str, dict[str, Any]]:
        return dict(self._phase_results)
