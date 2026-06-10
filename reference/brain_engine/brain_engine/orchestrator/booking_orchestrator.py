"""Booking Orchestrator — autonomous workflow after booking/new.

One POST to /api/v1/booking/new triggers this orchestrator which then
autonomously manages the entire turnover process via Telegram:

  1. Contact all cleaners -> wait 3 min for responses
  2. If multiple available -> ask PMS to choose (retry after 2 min)
  3. Dispatch selected cleaner with access codes
  4. Wait for photos + /done
  5. If issues found -> contact vendor (OPS)
  6. Notify guest + PMS on completion
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from brain_engine.api import mockup_loader

logger = logging.getLogger(__name__)

# Turkish keywords for parsing natural language responses
_AVAILABLE_KEYWORDS = {
    "müsait", "evet", "gelebilirim", "tamam", "ok", "olur",
    "uygun", "available", "yes", "musait", "musaitim", "müsaitim",
}
_BUSY_KEYWORDS = {
    "meşgul", "hayır", "hayir", "gelemem", "yok", "olmaz", "busy",
    "müsait değil", "musait degil", "no", "mesgul", "degil",
    "yapamam", "gelemiyorum", "olmaz", "bos degilim",
}
_ISSUE_KEYWORDS = {
    "kırık", "kirik", "broken", "arıza", "ariza", "su", "water",
    "leak", "tv", "televizyon", "çatlak", "catlak", "hasar",
    "damage", "bozuk", "sorun", "problem",
}

# Timeouts
CLEANER_WAIT_SECONDS = 180  # 3 minutes
PMS_WAIT_SECONDS = 120      # 2 minutes
PMS_MAX_RETRIES = 3
PHOTO_WAIT_SECONDS = 1800   # 30 minutes max for cleaning


class BookingOrchestrator:
    """Autonomous booking turnover orchestrator.

    Runs as an asyncio background task. Communicates with cleaners,
    PMS users, and vendors via Telegram bot. Tracks all state in
    the active process store.

    Attributes:
        process_id: Active process identifier.
        property_id: Property being cleaned.
        guest_name: Incoming guest name.
    """

    def __init__(
        self,
        *,
        process_id: str,
        property_id: str,
        guest_name: str,
        telegram_bot: Any,
        process_store: Any,
        router: Any,
    ) -> None:
        self.process_id = process_id
        self.property_id = property_id
        self.guest_name = guest_name
        self._bot = telegram_bot
        self._store = process_store
        self._router = router

        # Response collection
        self._responses: dict[str, str] = {}
        self._response_events: dict[str, asyncio.Event] = {}
        self._any_response_event = asyncio.Event()

        # Photo collection
        self._photos: list[dict[str, Any]] = []
        self._done_event = asyncio.Event()
        self._done_notes: str = ""

        # State
        self._selected_cleaner: dict[str, Any] = {}
        self._pms_response: str = ""
        self._pms_event = asyncio.Event()

    # ── Public API (called by message handler) ────────────── #

    def deliver_text(self, chat_id: str, text: str) -> None:
        """Deliver a text message from Telegram to this orchestrator.

        Args:
            chat_id: Sender's Telegram chat ID.
            text: Message text.
        """
        self._responses[chat_id] = text
        event = self._response_events.get(chat_id)
        if event:
            event.set()
        self._any_response_event.set()
        logger.info(
            "[Orch %s] Text from chat_id=%s: %s",
            self.process_id, chat_id, text[:80],
        )

    def deliver_pms_response(self, chat_id: str, text: str) -> None:
        """Deliver PMS user's selection response.

        Args:
            chat_id: PMS user's chat ID.
            text: Selection text.
        """
        self._pms_response = text
        self._pms_event.set()
        logger.info("[Orch %s] PMS response: %s", self.process_id, text[:80])

    def deliver_photo(
        self,
        chat_id: str,
        file_id: str,
        caption: str,
    ) -> None:
        """Deliver a photo from Telegram.

        Args:
            chat_id: Sender's chat ID.
            file_id: Telegram file_id.
            caption: Photo caption.
        """
        self._photos.append({
            "file_id": file_id,
            "caption": caption,
            "from_chat_id": chat_id,
            "received_at": _now_iso(),
        })
        logger.info(
            "[Orch %s] Photo #%d from chat_id=%s",
            self.process_id, len(self._photos), chat_id,
        )

    def deliver_done(self, chat_id: str, notes: str = "") -> None:
        """Signal that cleaner typed /done.

        Args:
            chat_id: Cleaner's chat ID.
            notes: Any notes sent with /done.
        """
        self._done_notes = notes
        self._done_event.set()
        logger.info("[Orch %s] /done from chat_id=%s", self.process_id, chat_id)

    # ── Main workflow ─────────────────────────────────────── #

    async def run(self) -> None:
        """Execute the full autonomous booking workflow."""
        try:
            await self._log_history("orchestrator_started", "Autonomous workflow started")

            # Phase 1: Contact cleaners
            cleaners = mockup_loader.get_cleaners_for_property(self.property_id)
            registered = self._filter_registered_cleaners(cleaners)
            if not registered:
                await self._handle_no_registered_cleaners(cleaners)
                return

            await self._contact_cleaners(registered)

            # Phase 2: Wait for responses
            available = await self._collect_cleaner_responses(registered)
            if not available:
                await self._handle_no_available_cleaners()
                return

            # Phase 3: Select cleaner
            selected = await self._select_cleaner(available)
            if not selected:
                await self._handle_selection_failed()
                return
            self._selected_cleaner = selected

            # Phase 4: Dispatch cleaner
            await self._dispatch_cleaner(selected)

            # Phase 5: Wait for photos + /done
            report = await self._wait_for_cleaning_report(selected)

            # Phase 6: Check issues -> OPS
            issues = self._detect_issues(report)
            if issues:
                await self._trigger_ops(issues)

            # Phase 7: Notify completion
            await self._notify_completion(issues)
            await self._log_history("workflow_completed", "Full workflow completed autonomously")

        except asyncio.CancelledError:
            await self._log_history("workflow_cancelled", "Workflow was cancelled")
        except Exception as exc:
            logger.error("[Orch %s] Workflow failed: %s", self.process_id, exc, exc_info=True)
            await self._log_history("workflow_error", f"Error: {exc}")
        finally:
            self._router.unregister_process(self.process_id)

    # ── Phase 1: Contact cleaners ─────────────────────────── #

    def _filter_registered_cleaners(
        self,
        cleaners: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep only cleaners with a Telegram chat_id set.

        Args:
            cleaners: All cleaners for the property.

        Returns:
            Cleaners that have telegram_chat_id.
        """
        registered = [
            c for c in cleaners
            if c.get("telegram_chat_id")
        ]
        logger.info(
            "[Orch %s] %d/%d cleaners have Telegram",
            self.process_id, len(registered), len(cleaners),
        )
        return registered

    async def _contact_cleaners(
        self,
        cleaners: list[dict[str, Any]],
    ) -> None:
        """Send availability request to all registered cleaners.

        Args:
            cleaners: Cleaners with telegram_chat_id.
        """
        prop = mockup_loader.get_property(self.property_id)
        prop_name = prop.get("name", self.property_id)

        for cleaner in cleaners:
            chat_id = str(cleaner["telegram_chat_id"])
            name = cleaner.get("name", "")
            event = asyncio.Event()
            self._response_events[chat_id] = event
            self._router.register(chat_id, self)

            msg = (
                f"Merhaba {name}!\n\n"
                f"<b>{prop_name}</b> icin temizlik var.\n"
                f"Misafir: {self.guest_name}\n\n"
                f"Musait misiniz? (evet/hayir)"
            )
            await self._send_telegram(chat_id, msg)

        names = ", ".join(c["name"] for c in cleaners)
        await self._log_history(
            "cleaners_contacted",
            f"Sent availability request to: {names}",
        )

    # ── Phase 2: Collect responses ────────────────────────── #

    async def _collect_cleaner_responses(
        self,
        cleaners: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Wait up to 3 minutes for cleaner responses.

        Args:
            cleaners: Contacted cleaners.

        Returns:
            List of cleaners that responded 'available'.
        """
        await self._log_history(
            "waiting_responses",
            f"Waiting {CLEANER_WAIT_SECONDS}s for cleaner responses...",
        )

        await asyncio.sleep(CLEANER_WAIT_SECONDS)

        available: list[dict[str, Any]] = []
        for cleaner in cleaners:
            chat_id = str(cleaner["telegram_chat_id"])
            text = self._responses.get(chat_id, "")
            status = self._parse_availability(text)
            if status == "available":
                available.append(cleaner)
            log_detail = f"{cleaner['name']}: {status} ('{text[:40]}')"
            await self._log_history("cleaner_responded", log_detail)

        await self._log_history(
            "responses_collected",
            f"{len(available)}/{len(cleaners)} available",
        )
        return available

    def _parse_availability(self, text: str) -> str:
        """Parse cleaner's natural language response.

        Args:
            text: Message text from cleaner.

        Returns:
            'available', 'busy', or 'no_response'.
        """
        if not text:
            return "no_response"
        lower = text.lower().strip()
        # Check busy FIRST — reject keywords take priority
        for kw in _BUSY_KEYWORDS:
            if kw in lower:
                return "busy"
        for kw in _AVAILABLE_KEYWORDS:
            if kw in lower:
                return "available"
        # Default: unknown response, treat as no_response
        return "no_response"

    # ── Phase 3: Select cleaner ───────────────────────────── #

    async def _select_cleaner(
        self,
        available: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Select cleaner — auto if one, ask PMS if multiple.

        Args:
            available: Available cleaners.

        Returns:
            Selected cleaner dict, or empty dict on failure.
        """
        if len(available) == 1:
            name = available[0]["name"]
            await self._log_history("cleaner_auto_selected", f"{name} (only one available)")
            return available[0]

        return await self._ask_pms_to_select(available)

    async def _ask_pms_to_select(
        self,
        available: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask PMS user to select from multiple available cleaners.

        Sends Telegram message to PMS, waits 2 min, retries up to 3 times.

        Args:
            available: Available cleaners.

        Returns:
            Selected cleaner dict, or empty dict.
        """
        pms = mockup_loader.get_pms_user(self.property_id)
        pms_chat_id = str(pms.get("telegram_chat_id", ""))
        pms_name = pms.get("name", "PMS")

        if not pms_chat_id:
            await self._log_history("pms_no_telegram", f"{pms_name} has no Telegram — auto-selecting best rated")
            return max(available, key=lambda c: c.get("rating", 0))

        self._router.register(pms_chat_id, self)
        options = self._build_cleaner_options(available)

        for attempt in range(1, PMS_MAX_RETRIES + 1):
            self._pms_event.clear()
            self._pms_response = ""

            msg = (
                f"Merhaba {pms_name}!\n\n"
                f"Birden fazla temizlikci musait:\n{options}\n\n"
                f"Hangisini gondereyim? (isim veya numara yazin)"
            )
            await self._send_telegram(pms_chat_id, msg)
            await self._log_history(
                "pms_asked",
                f"Asked {pms_name} to select (attempt {attempt}/{PMS_MAX_RETRIES})",
            )

            try:
                await asyncio.wait_for(self._pms_event.wait(), timeout=PMS_WAIT_SECONDS)
            except asyncio.TimeoutError:
                if attempt < PMS_MAX_RETRIES:
                    await self._send_telegram(pms_chat_id, f"{pms_name}, cevabinizi bekliyorum...")
                    continue
                await self._log_history("pms_timeout", f"{pms_name} did not respond — auto-selecting")
                return max(available, key=lambda c: c.get("rating", 0))

            selected = self._match_pms_selection(self._pms_response, available)
            if selected:
                await self._log_history(
                    "cleaner_selected_by_pms",
                    f"{pms_name} selected {selected['name']}",
                )
                return selected

            await self._send_telegram(pms_chat_id, "Anlamadim, lutfen isim veya numara yazin.")

        return max(available, key=lambda c: c.get("rating", 0))

    def _build_cleaner_options(self, available: list[dict[str, Any]]) -> str:
        """Format numbered list of available cleaners.

        Args:
            available: Available cleaner dicts.

        Returns:
            Formatted string like "1. Aybuke (4.8)\n2. Mumin (4.6)".
        """
        lines = []
        for i, c in enumerate(available, 1):
            lines.append(f"{i}. {c['name']} (rating: {c.get('rating', '?')})")
        return "\n".join(lines)

    def _match_pms_selection(
        self,
        response: str,
        available: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Match PMS response to a cleaner.

        Args:
            response: PMS user's text (name or number).
            available: Available cleaners.

        Returns:
            Matched cleaner or None.
        """
        text = response.strip().lower()

        # Try number
        try:
            idx = int(text) - 1
            if 0 <= idx < len(available):
                return available[idx]
        except ValueError:
            pass

        # Try name match
        for c in available:
            if c["name"].lower() in text or text in c["name"].lower():
                return c

        return None

    # ── Phase 4: Dispatch cleaner ─────────────────────────── #

    async def _dispatch_cleaner(self, cleaner: dict[str, Any]) -> None:
        """Send cleaner to property with access codes and instructions.

        Args:
            cleaner: Selected cleaner dict.
        """
        chat_id = str(cleaner["telegram_chat_id"])
        name = cleaner["name"]
        access = mockup_loader.get_property_access(self.property_id)
        prop = mockup_loader.get_property(self.property_id)

        msg = (
            f"<b>Gorev onaylandi!</b>\n\n"
            f"<b>Adres:</b> {prop.get('address', '')}\n"
            f"<b>Bina kodu:</b> {access.get('building_door_code', 'N/A')}\n"
            f"<b>Lockbox kodu:</b> {access.get('lockbox_code', 'N/A')}\n"
            f"<b>WiFi:</b> {access.get('wifi_name', '')} / {access.get('wifi_password', '')}\n\n"
            f"Temizlik bitince fotograflari gonder ve /done yaz.\n"
            f"Sorun varsa (kirik, ariza vs.) mutlaka yaz!"
        )
        await self._send_telegram(chat_id, msg)

        # Make sure we route /done and photos from this cleaner
        self._router.register(chat_id, self)

        await self._log_history(
            "cleaner_dispatched",
            f"{name} dispatched with access codes",
        )

    # ── Phase 5: Wait for cleaning report ─────────────────── #

    async def _wait_for_cleaning_report(
        self,
        cleaner: dict[str, Any],
    ) -> dict[str, Any]:
        """Wait for cleaner to send photos and /done.

        Args:
            cleaner: Dispatched cleaner.

        Returns:
            Report dict with photos and notes.
        """
        chat_id = str(cleaner["telegram_chat_id"])
        name = cleaner["name"]
        await self._log_history("waiting_report", f"Waiting for {name} to finish cleaning...")

        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=PHOTO_WAIT_SECONDS)
        except asyncio.TimeoutError:
            await self._send_telegram(chat_id, f"{name}, temizlik bitti mi? /done yazmayi unutmayin.")
            try:
                await asyncio.wait_for(self._done_event.wait(), timeout=PHOTO_WAIT_SECONDS)
            except asyncio.TimeoutError:
                await self._log_history("report_timeout", f"{name} did not send /done")

        report = {
            "cleaner_name": name,
            "photos": self._photos,
            "notes": self._done_notes,
            "photo_count": len(self._photos),
            "completed_at": _now_iso(),
        }
        await self._log_history(
            "cleaning_reported",
            f"{name}: {len(self._photos)} photos, notes: {self._done_notes[:80]}",
        )
        return report

    # ── Phase 6: Issue detection + OPS ────────────────────── #

    def _detect_issues(self, report: dict[str, Any]) -> list[str]:
        """Detect issues from cleaner notes.

        Args:
            report: Cleaning report.

        Returns:
            List of detected issue keywords.
        """
        notes = report.get("notes", "").lower()
        captions = " ".join(
            p.get("caption", "") for p in report.get("photos", [])
        ).lower()
        text = f"{notes} {captions}"

        found = []
        for kw in _ISSUE_KEYWORDS:
            if kw in text:
                found.append(kw)
        return found

    async def _trigger_ops(self, issues: list[str]) -> None:
        """Contact vendor for detected issues.

        Args:
            issues: List of issue keywords.
        """
        await self._log_history("ops_triggered", f"Issues: {', '.join(issues)}")

        vendors = mockup_loader.get_vendors_for_property(self.property_id)
        vendor = self._match_vendor(vendors, issues)
        if not vendor:
            await self._log_history("no_vendor", "No matching vendor found")
            return

        vendor_chat_id = str(vendor.get("telegram_chat_id", ""))
        vendor_name = vendor.get("name", "Vendor")

        if vendor_chat_id:
            msg = (
                f"Merhaba {vendor_name}!\n\n"
                f"<b>{self.property_id}</b> adresinde ariza bildirildi:\n"
                f"Sorunlar: {', '.join(issues)}\n\n"
                f"Musait misiniz? Tahmini sure ve maliyet?"
            )
            await self._send_telegram(vendor_chat_id, msg)
            await self._log_history("vendor_contacted", f"{vendor_name} contacted for {issues}")
        else:
            await self._log_history(
                "vendor_no_telegram",
                f"{vendor_name} has no Telegram — manual contact needed: {vendor.get('phone', '')}",
            )

        # Notify PMS about issues
        await self._notify_pms(
            f"Dikkat! Sorun bildirildi: {', '.join(issues)}.\n"
            f"Vendor {vendor_name} ile iletisime gecildi.",
        )

    def _match_vendor(
        self,
        vendors: list[dict[str, Any]],
        issues: list[str],
    ) -> dict[str, Any] | None:
        """Match best vendor for issues.

        Args:
            vendors: Available vendors.
            issues: Issue keywords.

        Returns:
            Best matching vendor or None.
        """
        issue_text = " ".join(issues).lower()
        electrical = {"tv", "televizyon", "kırık", "kirik", "broken", "elektrik", "bozuk"}
        plumbing = {"su", "water", "leak", "tesisat", "boru"}

        if any(kw in issue_text for kw in electrical):
            for v in vendors:
                specs = v.get("specialty", [])
                if any("electr" in s.lower() or "tv" in s.lower() for s in specs):
                    return v

        if any(kw in issue_text for kw in plumbing):
            for v in vendors:
                specs = v.get("specialty", [])
                if any("plumb" in s.lower() or "water" in s.lower() for s in specs):
                    return v

        return vendors[0] if vendors else None

    # ── Phase 7: Notifications ────────────────────────────── #

    async def _notify_completion(self, issues: list[str]) -> None:
        """Send completion notifications to guest and PMS.

        Args:
            issues: Any issues found (may be empty).
        """
        access = mockup_loader.get_property_access(self.property_id)
        wifi = f"{access.get('wifi_name', '')} / {access.get('wifi_password', '')}"

        # Notify PMS
        issue_note = ""
        if issues:
            issue_note = f"\nSorunlar: {', '.join(issues)} — vendor bilgilendirildi."
        await self._notify_pms(
            f"Temizlik tamamlandi!\n"
            f"Temizlikci: {self._selected_cleaner.get('name', '?')}\n"
            f"Foto: {len(self._photos)} adet{issue_note}",
        )

        await self._log_history(
            "notifications_sent",
            f"PMS notified. Guest: {self.guest_name}. Issues: {len(issues)}",
        )

    async def _notify_pms(self, text: str) -> None:
        """Send a message to PMS user via Telegram.

        Args:
            text: Message text.
        """
        pms = mockup_loader.get_pms_user(self.property_id)
        pms_chat_id = str(pms.get("telegram_chat_id", ""))
        if pms_chat_id:
            await self._send_telegram(pms_chat_id, f"<b>[Brain Engine]</b>\n{text}")

    # ── Error handlers ────────────────────────────────────── #

    async def _handle_no_registered_cleaners(
        self,
        cleaners: list[dict[str, Any]],
    ) -> None:
        """Handle case when no cleaners have Telegram registered.

        Args:
            cleaners: All cleaners (without chat_ids).
        """
        names = ", ".join(f"{c['name']} ({c.get('phone', '')})" for c in cleaners)
        detail = f"No cleaners with Telegram. Manual contact needed: {names}"
        await self._log_history("no_registered_cleaners", detail)
        await self._notify_pms(
            f"Temizlikci bulunamadi (Telegram kayitli degil).\n"
            f"Manuel iletisim: {names}",
        )

    async def _handle_no_available_cleaners(self) -> None:
        """Handle case when all cleaners responded busy."""
        await self._log_history("no_available_cleaners", "All cleaners busy or no response")
        await self._notify_pms("Hicbir temizlikci musait degil. Manuel aksiyon gerekli.")

    async def _handle_selection_failed(self) -> None:
        """Handle case when cleaner selection failed."""
        await self._log_history("selection_failed", "Could not select a cleaner")
        await self._notify_pms("Temizlikci secimi basarisiz. Manuel aksiyon gerekli.")

    # ── Helpers ────────────────────────────────────────────── #

    async def _send_telegram(self, chat_id: str, text: str) -> None:
        """Send a Telegram message, handling errors gracefully.

        Args:
            chat_id: Target chat ID.
            text: Message text (HTML).
        """
        try:
            await self._bot.send_message(chat_id=int(chat_id), text=text, parse_mode="HTML")
        except Exception as exc:
            logger.error(
                "[Orch %s] Telegram send failed to %s: %s",
                self.process_id, chat_id, exc,
            )

    async def _log_history(self, event: str, detail: str) -> None:
        """Append event to process history in store.

        Args:
            event: Event name.
            detail: Description.
        """
        logger.info("[Orch %s] %s: %s", self.process_id, event, detail)
        if not self._store:
            return
        try:
            process = await self._store.get(self.process_id)
            if not process:
                return
            process.setdefault("history", []).append({
                "time": _now_iso(),
                "event": event,
                "detail": detail,
            })
            process.setdefault("context", {})["current_step"] = event
            await self._save_process(process)
        except Exception as exc:
            logger.error("[Orch %s] Failed to log history: %s", self.process_id, exc)

    async def _save_process(self, process: dict[str, Any]) -> None:
        """Save process to store.

        Args:
            process: Process dict.
        """
        import json
        pid = process.get("process_id", "")
        await self._store._redis.set(self._store._key(pid), json.dumps(process))


def _now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()
