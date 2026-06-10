"""Workflow Endpoints — cleaner dispatch, PMS approval, vendor ops.

Full post-booking workflow:
  1. booking/new → auto-dispatch to cleaners
  2. cleaner/respond → cleaner accepts/declines
  3. pms/select-cleaner → PMS user picks cleaner (if multiple available)
  4. cleaner/report → cleaner submits photos + notes (damage triggers OPS)
  5. vendor/respond → vendor accepts repair job
  6. workflow/status → view full workflow state

All mockup data (properties, cleaners, vendors, PMS users) is loaded
from config/*.json via mockup_loader.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from brain_engine.api import mockup_loader
from brain_engine.api.models import ActiveProcessResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Workflow"])

# Shared deps — injected from server.py
_deps: dict[str, Any] = {}


def configure_workflow_deps(deps: dict[str, Any]) -> None:
    """Inject shared dependencies.

    Args:
        deps: Dependency dict from server startup.
    """
    _deps.update(deps)


# ── Request/Response Models ────────────────────────────────────── #


class CleanerRespondRequest(BaseModel):
    """Cleaner responds to availability request.

    Attributes:
        process_id: Active process ID.
        cleaner_id: Cleaner contact ID.
        response: available or busy.
        message: Optional message from cleaner.
    """

    process_id: str
    cleaner_id: str
    response: str = "available"
    message: str = ""


class PMSSelectCleanerRequest(BaseModel):
    """PMS user selects which cleaner to send.

    Attributes:
        process_id: Active process ID.
        selected_cleaner_id: Chosen cleaner contact ID.
        pms_note: Optional note from PMS user.
    """

    process_id: str
    selected_cleaner_id: str
    pms_note: str = ""


class CleanerReportRequest(BaseModel):
    """Cleaner submits cleaning report with photos.

    Attributes:
        process_id: Active process ID.
        cleaner_id: Cleaner contact ID.
        status: completed, in_progress, issue_found.
        photos: List of photo URLs/filenames.
        notes: Cleaner notes about the cleaning.
        issues_found: List of issues (broken_tv, water_leak, etc.).
    """

    process_id: str
    cleaner_id: str
    status: str = "completed"
    photos: list[str] = Field(default_factory=list)
    notes: str = ""
    issues_found: list[str] = Field(default_factory=list)


class VendorRespondRequest(BaseModel):
    """Vendor responds to repair request.

    Attributes:
        process_id: Active process ID.
        vendor_id: Vendor contact ID.
        response: available or busy.
        eta_minutes: Estimated arrival time in minutes.
        cost_estimate: Estimated repair cost.
        message: Optional message from vendor.
    """

    process_id: str
    vendor_id: str
    response: str = "available"
    eta_minutes: int = 60
    cost_estimate: float = 0.0
    message: str = ""


class WorkflowStatusResponse(BaseModel):
    """Full workflow status.

    Attributes:
        process: Active process details.
        property_data: Property mockup data.
        cleaners_contacted: Cleaners that were contacted.
        cleaner_responses: Responses received.
        selected_cleaner: Which cleaner was selected.
        cleaning_report: Cleaning report if submitted.
        ops_triggered: Whether OPS was triggered.
        vendor_dispatched: Vendor info if dispatched.
        notifications_sent: List of notifications sent.
        current_step: Current workflow step.
    """

    process: dict[str, Any] = Field(default_factory=dict)
    property_data: dict[str, Any] = Field(default_factory=dict)
    cleaners_contacted: list[dict[str, Any]] = Field(default_factory=list)
    cleaner_responses: list[dict[str, Any]] = Field(default_factory=list)
    selected_cleaner: dict[str, Any] = Field(default_factory=dict)
    cleaning_report: dict[str, Any] = Field(default_factory=dict)
    ops_triggered: bool = False
    vendor_dispatched: dict[str, Any] = Field(default_factory=dict)
    notifications_sent: list[dict[str, Any]] = Field(default_factory=list)
    current_step: str = ""


# ── Endpoints ──────────────────────────────────────────────────── #


@router.post(
    "/cleaner/respond",
    response_model=dict[str, Any],
    tags=["Workflow"],
    summary="Cleaner responds to availability request",
    description=(
        "After booking/new dispatches to cleaners, each cleaner "
        "responds with available/busy. If multiple respond available, "
        "PMS user must select one."
    ),
)
async def cleaner_respond(
    request: CleanerRespondRequest,
) -> dict[str, Any]:
    """Handle cleaner availability response."""
    store = _deps.get("active_process_store")
    if not store:
        raise HTTPException(status_code=503, detail="Process store unavailable")

    process = await store.get(request.process_id)
    if not process:
        raise HTTPException(status_code=404, detail=f"Process {request.process_id} not found")

    cleaner = mockup_loader.get_cleaner(request.cleaner_id)
    cleaner_name = cleaner.get("name", request.cleaner_id)
    now = datetime.now(timezone.utc).isoformat()

    # Record response in process context
    responses = process.get("context", {}).get("cleaner_responses", [])
    responses.append({
        "cleaner_id": request.cleaner_id,
        "cleaner_name": cleaner_name,
        "response": request.response,
        "message": request.message,
        "responded_at": now,
    })
    process.setdefault("context", {})["cleaner_responses"] = responses

    # Update participant status
    for p in process.get("participants", []):
        if p.get("contact_id") == request.cleaner_id:
            p["status"] = request.response
            p["last_message"] = request.message
            p["last_message_at"] = now

    # Add history entry
    process["history"].append({
        "time": now,
        "event": "cleaner_responded",
        "detail": f"{cleaner_name}: {request.response} — {request.message}",
    })

    # Check how many are available
    available = [r for r in responses if r["response"] == "available"]

    if len(available) == 1:
        # Auto-select the only available cleaner
        selected = available[0]
        process["context"]["selected_cleaner"] = selected
        process["context"]["current_step"] = "cleaner_selected"
        process["history"].append({
            "time": now,
            "event": "cleaner_auto_selected",
            "detail": f"{selected['cleaner_name']} auto-selected (only one available)",
        })
        action = "cleaner_auto_selected"
    elif len(available) > 1:
        # Multiple available — need PMS approval
        process["context"]["current_step"] = "waiting_pms_selection"
        pms = mockup_loader.get_pms_user(
            process.get("property_id", ""),
        )
        process["context"]["pms_notified"] = True
        process["history"].append({
            "time": now,
            "event": "pms_selection_needed",
            "detail": (
                f"{len(available)} cleaners available: "
                f"{', '.join(r['cleaner_name'] for r in available)}. "
                f"Waiting for {pms.get('name', 'PMS')} to select."
            ),
        })
        action = "waiting_pms_selection"
    else:
        process["context"]["current_step"] = "waiting_cleaner_responses"
        action = "waiting_more_responses"

    await _save_process(store, process)

    return {
        "status": "ok",
        "action": action,
        "process_id": request.process_id,
        "cleaner": cleaner_name,
        "response": request.response,
        "available_cleaners": [r["cleaner_name"] for r in available],
        "total_responses": len(responses),
        "next_step": (
            "POST /api/v1/pms/select-cleaner" if action == "waiting_pms_selection"
            else "Waiting for cleaner to complete cleaning"
            if action == "cleaner_auto_selected"
            else "Waiting for more cleaner responses"
        ),
    }


@router.post(
    "/pms/select-cleaner",
    response_model=dict[str, Any],
    tags=["Workflow"],
    summary="PMS user selects cleaner",
    description=(
        "When multiple cleaners are available, PMS user (e.g. Can) "
        "selects which one to send. If PMS doesn't respond in 2 min, "
        "the question should be repeated."
    ),
)
async def pms_select_cleaner(
    request: PMSSelectCleanerRequest,
) -> dict[str, Any]:
    """PMS user selects which cleaner to dispatch."""
    store = _deps.get("active_process_store")
    if not store:
        raise HTTPException(status_code=503, detail="Process store unavailable")

    process = await store.get(request.process_id)
    if not process:
        raise HTTPException(status_code=404, detail=f"Process {request.process_id} not found")

    cleaner = mockup_loader.get_cleaner(request.selected_cleaner_id)
    cleaner_name = cleaner.get("name", request.selected_cleaner_id)
    pms = mockup_loader.get_pms_user(
        process.get("property_id", ""),
    )
    pms_name = pms.get("name", "PMS")
    now = datetime.now(timezone.utc).isoformat()

    process.setdefault("context", {})
    process["context"]["selected_cleaner"] = {
        "cleaner_id": request.selected_cleaner_id,
        "cleaner_name": cleaner_name,
        "selected_by": pms_name,
        "selected_at": now,
        "pms_note": request.pms_note,
    }
    process["context"]["current_step"] = "cleaner_dispatched"

    process["history"].append({
        "time": now,
        "event": "cleaner_selected_by_pms",
        "detail": (
            f"{pms_name} selected {cleaner_name}."
            f"{' Note: ' + request.pms_note if request.pms_note else ''}"
        ),
    })
    process["history"].append({
        "time": now,
        "event": "cleaner_dispatched",
        "detail": f"{cleaner_name} dispatched to property. Waiting for cleaning report + photos.",
    })

    await _save_process(store, process)

    return {
        "status": "ok",
        "action": "cleaner_dispatched",
        "process_id": request.process_id,
        "selected_cleaner": cleaner_name,
        "selected_by": pms_name,
        "message_to_cleaner": (
            f"Merhaba {cleaner_name}, temizlige gidebilirsin. "
            f"Bittikten sonra lutfen fotograflari gonder."
        ),
        "next_step": "POST /api/v1/cleaner/report (cleaner submits photos + notes)",
    }


@router.post(
    "/cleaner/report",
    response_model=dict[str, Any],
    tags=["Workflow"],
    summary="Cleaner submits cleaning report",
    description=(
        "After cleaning, the cleaner submits photos and notes. "
        "If issues are found (broken TV, water leak, etc.), "
        "OPS workflow is triggered and vendor is dispatched from mockup data."
    ),
)
async def cleaner_report(
    request: CleanerReportRequest,
) -> dict[str, Any]:
    """Handle cleaner report with photos and damage detection."""
    store = _deps.get("active_process_store")
    if not store:
        raise HTTPException(status_code=503, detail="Process store unavailable")

    process = await store.get(request.process_id)
    if not process:
        raise HTTPException(status_code=404, detail=f"Process {request.process_id} not found")

    cleaner = mockup_loader.get_cleaner(request.cleaner_id)
    cleaner_name = cleaner.get("name", request.cleaner_id)
    property_id = process.get("property_id", "")
    pms = mockup_loader.get_pms_user(property_id)
    now = datetime.now(timezone.utc).isoformat()

    # Store cleaning report
    report = {
        "cleaner_id": request.cleaner_id,
        "cleaner_name": cleaner_name,
        "status": request.status,
        "photos": request.photos,
        "notes": request.notes,
        "issues_found": request.issues_found,
        "submitted_at": now,
    }
    process.setdefault("context", {})
    process["context"]["cleaning_report"] = report
    process["context"]["current_step"] = "cleaning_reported"

    process["history"].append({
        "time": now,
        "event": "cleaning_report_submitted",
        "detail": (
            f"{cleaner_name}: {request.status}. "
            f"Photos: {len(request.photos)}. "
            f"Notes: {request.notes}"
        ),
    })

    notifications: list[dict[str, Any]] = []
    ops_triggered = False
    vendor_info: dict[str, Any] = {}

    # If issues found → trigger OPS and dispatch vendor
    if request.issues_found:
        ops_triggered = True
        process["context"]["current_step"] = "ops_triggered"

        vendors = mockup_loader.get_vendors_for_property(property_id)
        if vendors:
            vendor = _match_vendor(vendors, request.issues_found)
            vendor_info = {
                "vendor_id": vendor.get("contact_id", ""),
                "vendor_name": vendor.get("name", ""),
                "specialty": vendor.get("specialty", []),
                "issues": request.issues_found,
                "status": "contacted",
            }
            process["context"]["vendor_dispatched"] = vendor_info

        process["history"].append({
            "time": now,
            "event": "ops_triggered",
            "detail": (
                f"Issues found: {', '.join(request.issues_found)}. "
                f"Vendor contacted: {vendor_info.get('vendor_name', 'N/A')}"
            ),
        })

        # Notify PMS about issues
        notifications.append({
            "to": pms.get("name", "PMS"),
            "channel": "telegram",
            "message": (
                f"Dikkat! {cleaner_name} sorun bildirdi: "
                f"{', '.join(request.issues_found)}. "
                f"Vendor {vendor_info.get('vendor_name', '')} ile iletisime gecildi."
            ),
            "sent_at": now,
        })

    # Notify guest that cleaning is done
    guest_name = process.get("context", {}).get("guest_name", "Guest")
    notifications.append({
        "to": guest_name,
        "channel": "whatsapp",
        "message": (
            f"Hi {guest_name}, your apartment has been cleaned and is ready. "
            f"Welcome! WiFi: {_get_wifi_info(property_id)}"
        ),
        "sent_at": now,
    })

    # Notify PMS that cleaning is done
    notifications.append({
        "to": pms.get("name", "PMS"),
        "channel": "telegram",
        "message": (
            f"Temizlik tamamlandi. {cleaner_name} raporunu gonderdi. "
            f"Fotograflar: {len(request.photos)} adet."
            + (f" Sorunlar: {', '.join(request.issues_found)}" if request.issues_found else "")
        ),
        "sent_at": now,
    })

    process["context"]["notifications"] = process["context"].get("notifications", []) + notifications

    if not ops_triggered:
        process["status"] = "completed"
        process["completed_at"] = now
        process["context"]["current_step"] = "completed"

    process["history"].append({
        "time": now,
        "event": "notifications_sent",
        "detail": f"Notified: {', '.join(n['to'] for n in notifications)}",
    })

    await _save_process(store, process)

    return {
        "status": "ok",
        "action": "ops_triggered" if ops_triggered else "workflow_completed",
        "process_id": request.process_id,
        "cleaning_report": report,
        "ops_triggered": ops_triggered,
        "vendor_dispatched": vendor_info if ops_triggered else None,
        "notifications": notifications,
        "next_step": (
            "POST /api/v1/vendor/respond (vendor responds to repair request)"
            if ops_triggered
            else "Workflow complete. Guest and PMS notified."
        ),
    }


@router.post(
    "/vendor/respond",
    response_model=dict[str, Any],
    tags=["Workflow"],
    summary="Vendor responds to repair request",
    description=(
        "After OPS is triggered due to damage, vendor responds "
        "with availability, ETA, and cost estimate."
    ),
)
async def vendor_respond(
    request: VendorRespondRequest,
) -> dict[str, Any]:
    """Handle vendor response to repair request."""
    store = _deps.get("active_process_store")
    if not store:
        raise HTTPException(status_code=503, detail="Process store unavailable")

    process = await store.get(request.process_id)
    if not process:
        raise HTTPException(status_code=404, detail=f"Process {request.process_id} not found")

    vendor = mockup_loader.get_vendor(request.vendor_id)
    vendor_name = vendor.get("name", request.vendor_id)
    property_id = process.get("property_id", "")
    pms = mockup_loader.get_pms_user(property_id)
    now = datetime.now(timezone.utc).isoformat()

    process.setdefault("context", {})
    process["context"]["vendor_response"] = {
        "vendor_id": request.vendor_id,
        "vendor_name": vendor_name,
        "response": request.response,
        "eta_minutes": request.eta_minutes,
        "cost_estimate": request.cost_estimate,
        "message": request.message,
        "responded_at": now,
    }

    notifications: list[dict[str, Any]] = []

    if request.response == "available":
        process["context"]["current_step"] = "vendor_dispatched"
        process["history"].append({
            "time": now,
            "event": "vendor_accepted",
            "detail": (
                f"{vendor_name} accepted. ETA: {request.eta_minutes} min. "
                f"Cost: {request.cost_estimate} TL."
            ),
        })

        notifications.append({
            "to": pms.get("name", "PMS"),
            "channel": "telegram",
            "message": (
                f"{vendor_name} gelecek. Tahmini varis: {request.eta_minutes} dk. "
                f"Tahmini maliyet: {request.cost_estimate} TL."
            ),
            "sent_at": now,
        })

        process["status"] = "completed"
        process["completed_at"] = now
        process["context"]["current_step"] = "completed"
    else:
        process["context"]["current_step"] = "vendor_unavailable"
        process["history"].append({
            "time": now,
            "event": "vendor_unavailable",
            "detail": f"{vendor_name} unavailable: {request.message}",
        })

    process["context"]["notifications"] = process["context"].get("notifications", []) + notifications

    await _save_process(store, process)

    return {
        "status": "ok",
        "action": "vendor_dispatched" if request.response == "available" else "vendor_unavailable",
        "process_id": request.process_id,
        "vendor": vendor_name,
        "eta_minutes": request.eta_minutes,
        "cost_estimate": request.cost_estimate,
        "notifications": notifications,
        "next_step": "Workflow complete." if request.response == "available" else "Find another vendor.",
    }


@router.get(
    "/workflow/status/{process_id}",
    response_model=WorkflowStatusResponse,
    tags=["Workflow"],
    summary="Get full workflow status",
    description="Returns complete workflow state including all steps, responses, and notifications.",
)
async def workflow_status(process_id: str) -> WorkflowStatusResponse:
    """Get full workflow status for a process."""
    store = _deps.get("active_process_store")
    if not store:
        raise HTTPException(status_code=503, detail="Process store unavailable")

    process = await store.get(process_id)
    if not process:
        raise HTTPException(status_code=404, detail=f"Process {process_id} not found")

    property_id = process.get("property_id", "")
    ctx = process.get("context", {})

    return WorkflowStatusResponse(
        process=process,
        property_data=mockup_loader.get_property(property_id),
        cleaners_contacted=mockup_loader.get_cleaners_for_property(property_id),
        cleaner_responses=ctx.get("cleaner_responses", []),
        selected_cleaner=ctx.get("selected_cleaner", {}),
        cleaning_report=ctx.get("cleaning_report", {}),
        ops_triggered=bool(ctx.get("vendor_dispatched")),
        vendor_dispatched=ctx.get("vendor_dispatched", {}),
        notifications_sent=ctx.get("notifications", []),
        current_step=ctx.get("current_step", ""),
    )


@router.get(
    "/mockup/property/{property_id}",
    tags=["Workflow"],
    summary="View property mockup data",
)
async def get_mockup_property(property_id: str) -> dict[str, Any]:
    """Get property mockup data including access codes, PMS user."""
    data = mockup_loader.get_property(property_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Property {property_id} not found in mockup")
    return data


@router.get(
    "/mockup/cleaners/{property_id}",
    tags=["Workflow"],
    summary="View cleaners for property",
)
async def get_mockup_cleaners(property_id: str) -> list[dict[str, Any]]:
    """Get cleaners assigned to a property."""
    return mockup_loader.get_cleaners_for_property(property_id)


@router.get(
    "/mockup/vendors/{property_id}",
    tags=["Workflow"],
    summary="View vendors for property",
)
async def get_mockup_vendors(property_id: str) -> list[dict[str, Any]]:
    """Get vendors assigned to a property."""
    return mockup_loader.get_vendors_for_property(property_id)


class RegisterTelegramRequest(BaseModel):
    """Register a Telegram chat_id for a contact.

    Attributes:
        contact_id: Contact identifier (cleaner-aybuke, Can, vendor-elektrik).
        chat_id: Telegram chat ID.
    """

    contact_id: str
    chat_id: str


@router.post(
    "/register-telegram",
    response_model=dict[str, Any],
    tags=["Workflow"],
    summary="Register Telegram chat_id for a contact",
    description=(
        "Link a Telegram chat_id to a cleaner, vendor, or PMS user. "
        "This is required for the autonomous orchestrator to send "
        "messages via Telegram."
    ),
)
async def register_telegram(request: RegisterTelegramRequest) -> dict[str, Any]:
    """Register Telegram chat_id for a contact."""
    success = mockup_loader.update_chat_id(request.contact_id, request.chat_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Contact '{request.contact_id}' not found in mockup data",
        )
    return {
        "status": "ok",
        "contact_id": request.contact_id,
        "chat_id": request.chat_id,
        "message": f"Telegram chat_id registered for {request.contact_id}",
    }


@router.get(
    "/orchestrator/status",
    tags=["Workflow"],
    summary="Get autonomous orchestrator status",
    description="Shows active orchestrator count and routing info.",
)
async def orchestrator_status() -> dict[str, Any]:
    """Get orchestrator routing status."""
    from brain_engine.orchestrator.response_router import response_router
    return {
        "active_orchestrators": response_router.active_count,
        "registered_contacts": _get_registered_contacts(),
    }


def _get_registered_contacts() -> list[dict[str, str]]:
    """Get all contacts with Telegram chat_ids registered.

    Returns:
        List of dicts with contact_id, name, role, chat_id.
    """
    contacts: list[dict[str, str]] = []
    for c in mockup_loader.get_all_cleaners():
        if c.get("telegram_chat_id"):
            contacts.append({
                "contact_id": c.get("contact_id", ""),
                "name": c.get("name", ""),
                "role": "cleaner",
                "chat_id": str(c["telegram_chat_id"]),
            })
    for v in mockup_loader.get_all_vendors():
        if v.get("telegram_chat_id"):
            contacts.append({
                "contact_id": v.get("contact_id", ""),
                "name": v.get("name", ""),
                "role": "vendor",
                "chat_id": str(v["telegram_chat_id"]),
            })
    for prop in mockup_loader.get_all_properties():
        pms = prop.get("pms_user", {})
        if pms.get("telegram_chat_id"):
            contacts.append({
                "contact_id": pms.get("name", ""),
                "name": pms.get("name", ""),
                "role": "pms",
                "chat_id": str(pms["telegram_chat_id"]),
            })
    return contacts


# ── Helpers ────────────────────────────────────────────────────── #


def _match_vendor(
    vendors: list[dict[str, Any]],
    issues: list[str],
) -> dict[str, Any]:
    """Match the best vendor for given issues.

    Args:
        vendors: Available vendors.
        issues: List of issue keywords.

    Returns:
        Best matching vendor dict.
    """
    issue_text = " ".join(issues).lower()
    for v in vendors:
        specs = v.get("specialty", [])
        if isinstance(specs, str):
            specs = [specs]
        for s in specs:
            if s.lower() in issue_text or any(s.lower() in i.lower() for i in issues):
                return v
    # Electrical issues
    if any(kw in issue_text for kw in ["tv", "elektrik", "electrical", "appliance"]):
        for v in vendors:
            specs = v.get("specialty", [])
            if isinstance(specs, str):
                specs = [specs]
            if any("elektrik" in s.lower() or "electrical" in s.lower() or "tv" in s.lower() for s in specs):
                return v
    return vendors[0] if vendors else {}


def _get_wifi_info(property_id: str) -> str:
    """Get wifi info string for guest notification.

    Args:
        property_id: Property identifier.

    Returns:
        Wifi info string.
    """
    access = mockup_loader.get_property_access(property_id)
    if access:
        return f"{access.get('wifi_name', '')} / {access.get('wifi_password', '')}"
    return ""


async def _save_process(
    store: Any, process: dict[str, Any],
) -> None:
    """Save updated process back to store.

    Args:
        store: ActiveProcessStore instance.
        process: Process dict to save.
    """
    import json
    pid = process.get("process_id", "")
    await store._redis.set(store._key(pid), json.dumps(process))
