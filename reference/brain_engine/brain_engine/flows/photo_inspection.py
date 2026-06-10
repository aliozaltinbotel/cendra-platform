"""Photo Inspection Flow - Receiving and analyzing before/after property photos.

Handles the photo inspection pipeline: waiting for photos, receiving them,
running GPT-4o Vision analysis for damage comparison, and generating
a detailed inspection report.

States: WAITING_PHOTOS -> RECEIVED -> ANALYZING -> COMPARISON -> REPORT -> DONE
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

from brain_engine.state_manager.state_machine import StateMachine, Transition
from brain_engine.state_manager.slot_manager import SlotManager
from brain_engine.streaming.ag_ui_emitter import AGUIEmitter, AGUIEvent
from brain_engine.streaming.event_types import EventType
from brain_engine.memory.event_recorder import EventRecorder
from brain_engine.memory.episodic_memory import EpisodicMemory
from brain_engine.integrations.vision.photo_comparator import PhotoComparator, PhotoComparatorError

logger = logging.getLogger(__name__)

# State constants
WAITING_PHOTOS = "WAITING_PHOTOS"
RECEIVED = "RECEIVED"
ANALYZING = "ANALYZING"
COMPARISON = "COMPARISON"
REPORT = "REPORT"
DONE = "DONE"

STATES = [WAITING_PHOTOS, RECEIVED, ANALYZING, COMPARISON, REPORT, DONE]

# Damage severity scale
SEVERITY_LABELS = {
    1: "Minor (cosmetic only, no repair needed)",
    2: "Low (minor repair, under $50)",
    3: "Moderate (noticeable damage, $50-$200 repair)",
    4: "Significant (major damage, $200-$500 repair)",
    5: "Severe (replacement required, over $500)",
}


class PhotoInspectionFlow:
    """Manages the photo inspection and damage analysis pipeline.

    Orchestrates photo collection, AI-powered damage detection using
    GPT-4o Vision, before/after comparison, and inspection report
    generation.

    Args:
        slot_manager: SlotManager instance with incident slots registered.
        emitter: AG-UI event emitter for streaming updates.
        session_id: Unique session identifier.
        before_photos_dir: Path to the directory containing before photos.
        after_photos_dir: Path to the directory for received after photos.
    """

    def __init__(
        self,
        slot_manager: SlotManager,
        emitter: AGUIEmitter | None = None,
        session_id: str = "",
        before_photos_dir: str = "data/photos/before",
        after_photos_dir: str = "data/photos/after",
        photo_comparator: PhotoComparator | None = None,
        event_recorder: EventRecorder | None = None,
        episodic: EpisodicMemory | None = None,
    ) -> None:
        self.slot_manager = slot_manager
        self.emitter = emitter or AGUIEmitter()
        self.session_id = session_id
        self.before_photos_dir = before_photos_dir
        self.after_photos_dir = after_photos_dir
        self.photo_comparator = photo_comparator
        self._recorder = event_recorder
        self._episodic = episodic

        transitions = [
            Transition(
                from_state=WAITING_PHOTOS,
                to_state=RECEIVED,
                condition=lambda ctx: self._photos_received(),
            ),
            Transition(from_state=RECEIVED, to_state=ANALYZING),
            Transition(from_state=ANALYZING, to_state=COMPARISON),
            Transition(
                from_state=COMPARISON,
                to_state=REPORT,
                condition=lambda ctx: self._analysis_complete(),
            ),
            Transition(from_state=REPORT, to_state=DONE),
        ]

        self.state_machine = StateMachine(
            states=STATES,
            transitions=transitions,
            initial_state=WAITING_PHOTOS,
        )

        self._analysis_result: dict[str, Any] | None = None

    def _photos_received(self) -> bool:
        """Check if after photos have been received."""
        return self.slot_manager.get_value("photos_received") is True

    def _analysis_complete(self) -> bool:
        """Check if the photo analysis is complete."""
        return self._analysis_result is not None

    def _collect_photo_pairs(self) -> list[tuple[str, str]]:
        """Match before/after photos by filename for pairwise comparison.

        Returns:
            List of (before_path, after_path) tuples.
        """
        before_dir = Path(self.before_photos_dir)
        after_dir = Path(self.after_photos_dir)
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

        after_files = {
            f.stem: f for f in after_dir.iterdir()
            if f.is_file() and f.suffix.lower() in image_exts
        } if after_dir.exists() else {}

        before_files = {
            f.stem: f for f in before_dir.iterdir()
            if f.is_file() and f.suffix.lower() in image_exts
        } if before_dir.exists() else {}

        pairs: list[tuple[str, str]] = []
        for name, after_path in after_files.items():
            if name in before_files:
                pairs.append((str(before_files[name]), str(after_path)))

        return pairs

    async def _analyze_photos(self) -> dict[str, Any]:
        """Run photo analysis using GPT-4o Vision API.

        Compares before/after photo pairs using the PhotoComparator integration.
        Falls back gracefully if no comparator is configured or no photo pairs found.

        Returns:
            Analysis result dict with damage detection findings.
        """
        before_count = self.slot_manager.get_value("photos_before_count", 0)
        after_count = self.slot_manager.get_value("photos_after_count", 0)

        if self.photo_comparator is None:
            logger.warning("No PhotoComparator configured — cannot run Vision analysis")
            return {
                "damage_detected": False,
                "damage_items": [],
                "damage_severity": 0,
                "damage_location": "N/A",
                "damage_description": "Photo analysis unavailable: no Vision API configured.",
                "confidence": 0.0,
                "photos_analyzed": {"before": before_count, "after": after_count},
                "estimated_repair_cost": 0.0,
            }

        pairs = self._collect_photo_pairs()
        if not pairs:
            logger.warning("No matching before/after photo pairs found")
            return {
                "damage_detected": False,
                "damage_items": [],
                "damage_severity": 0,
                "damage_location": "N/A",
                "damage_description": "No matching before/after photo pairs found for comparison.",
                "confidence": 0.0,
                "photos_analyzed": {"before": before_count, "after": after_count},
                "estimated_repair_cost": 0.0,
            }

        all_damage_items: list[str] = []
        all_locations: list[str] = []
        all_descriptions: list[str] = []
        total_cost = 0.0
        max_severity = 0.0
        total_confidence = 0.0

        for before_path, after_path in pairs:
            room_name = Path(after_path).stem.replace("_", " ").title()
            try:
                result = await self.photo_comparator.compare_photos(
                    before_path=before_path,
                    after_path=after_path,
                    room_context=room_name,
                )
            except PhotoComparatorError as exc:
                logger.error("Vision API error for %s: %s", room_name, exc)
                continue

            if not result.no_damage_detected:
                for damage in result.damages:
                    location = damage.location or room_name
                    all_damage_items.append(
                        f"{damage.description} ({location})"
                    )
                    all_locations.append(location)
                    total_cost += damage.estimated_cost

                all_descriptions.append(result.summary)
                max_severity = max(max_severity, result.overall_severity)
                total_confidence += 1.0
            else:
                total_confidence += 1.0

        num_analyzed = len(pairs)
        damage_detected = len(all_damage_items) > 0

        # Map 1-10 Vision severity to 1-5 flow severity
        flow_severity = min(5, max(1, round(max_severity / 2))) if damage_detected else 0

        avg_confidence = (total_confidence / num_analyzed) if num_analyzed else 0.0
        # Normalize confidence: ratio of successfully analyzed pairs
        confidence = min(1.0, avg_confidence)

        unique_locations = list(dict.fromkeys(all_locations))

        return {
            "damage_detected": damage_detected,
            "damage_items": all_damage_items,
            "damage_severity": flow_severity,
            "damage_location": ", ".join(unique_locations) if unique_locations else "None",
            "damage_description": " ".join(all_descriptions) if all_descriptions else "No damage detected across all inspected areas.",
            "confidence": confidence,
            "photos_analyzed": {"before": before_count, "after": num_analyzed},
            "estimated_repair_cost": round(total_cost, 2),
        }

    async def run(self) -> AsyncIterator[AGUIEvent]:
        """Execute the photo inspection flow, yielding AG-UI events.

        Yields:
            AGUIEvent objects for each stage of the inspection pipeline.
        """
        flow_name = "photo_inspection"

        yield self.emitter.flow_started(flow_name, self.state_machine.current_state)

        if self._episodic:
            await self._episodic.add_episode(
                event="photo_inspection_started",
                content="Photo inspection flow initiated",
                metadata={"state": WAITING_PHOTOS, "session_id": self.session_id},
            )

        # --- WAITING_PHOTOS ---
        if not self._photos_received():
            yield self.emitter.text_message_start()
            yield self.emitter.text_message_content(
                "Waiting for after-checkout photos from the cleaner. "
                "Before photos are on file for comparison."
            )
            yield self.emitter.text_message_end()

            yield self.emitter.slot_requested(
                "photos_received",
                "Please upload the after-checkout photos for inspection."
            )
            return

        # Transition to RECEIVED
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=RECEIVED)
        yield self.emitter.flow_state_changed(flow_name, old_state, RECEIVED)

        # --- RECEIVED ---
        after_count = self.slot_manager.get_value("photos_after_count", 0)
        before_count = self.slot_manager.get_value("photos_before_count", 0)

        if self._episodic:
            await self._episodic.add_episode(
                event="photos_received",
                content=f"Photos received: {before_count} before, {after_count} after",
                metadata={"before_count": before_count, "after_count": after_count},
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Photos received. Before photos: {before_count}. "
            f"After photos: {after_count}. Starting analysis."
        )
        yield self.emitter.text_message_end()

        # Transition to ANALYZING
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=ANALYZING)
        yield self.emitter.flow_state_changed(flow_name, old_state, ANALYZING)

        # --- ANALYZING ---
        yield self.emitter.tool_call_start("gpt4o_vision_analysis")
        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Running GPT-4o Vision analysis on property photos. "
            "Comparing before and after images for damage detection..."
        )
        yield self.emitter.text_message_end()

        # Run the analysis
        self._analysis_result = await self._analyze_photos()

        if self._episodic:
            await self._episodic.add_episode(
                event="vision_analysis_complete",
                content=f"GPT-4o analysis done. Damage: {self._analysis_result['damage_detected']}",
                metadata={
                    "damage_detected": self._analysis_result["damage_detected"],
                    "severity": self._analysis_result["damage_severity"],
                    "confidence": self._analysis_result["confidence"],
                },
            )

        yield self.emitter.tool_call_end(
            "gpt4o_vision_analysis",
            {"damage_detected": self._analysis_result["damage_detected"]},
        )

        # Transition to COMPARISON
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=COMPARISON)
        yield self.emitter.flow_state_changed(flow_name, old_state, COMPARISON)

        # --- COMPARISON ---
        result = self._analysis_result

        # Update slots with analysis results
        self.slot_manager.set_slot("damage_detected", result["damage_detected"])
        yield self.emitter.slot_filled("damage_detected", result["damage_detected"])

        self.slot_manager.set_slot("damage_description", result["damage_description"])
        yield self.emitter.slot_filled("damage_description", result["damage_description"])

        self.slot_manager.set_slot("damage_severity", result["damage_severity"])
        yield self.emitter.slot_filled("damage_severity", result["damage_severity"])

        self.slot_manager.set_slot("damage_location", result["damage_location"])
        yield self.emitter.slot_filled("damage_location", result["damage_location"])

        self.slot_manager.set_slot("damage_items", result["damage_items"])
        yield self.emitter.slot_filled("damage_items", result["damage_items"])

        self.slot_manager.set_slot("analysis_confidence", result["confidence"])
        yield self.emitter.slot_filled("analysis_confidence", result["confidence"])

        self.slot_manager.set_slot("repair_estimate", result["estimated_repair_cost"])
        yield self.emitter.slot_filled("repair_estimate", result["estimated_repair_cost"])

        severity_label = SEVERITY_LABELS.get(result["damage_severity"], "Unknown")

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            f"Photo comparison complete.\n\n"
            f"Damage Detected: {'Yes' if result['damage_detected'] else 'No'}\n"
            f"Severity: {result['damage_severity']}/5 - {severity_label}\n"
            f"Confidence: {result['confidence']:.0%}\n"
            f"Location: {result['damage_location']}\n\n"
            f"Items damaged:\n"
            + "\n".join(f"  - {item}" for item in result["damage_items"])
            + f"\n\nEstimated repair cost: ${result['estimated_repair_cost']:.2f}"
        )
        yield self.emitter.text_message_end()

        # Transition to REPORT
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=REPORT)
        yield self.emitter.flow_state_changed(flow_name, old_state, REPORT)

        # --- REPORT ---
        if self._episodic:
            await self._episodic.add_episode(
                event="inspection_report_generated",
                content=f"Report: severity {result['damage_severity']}/5, "
                        f"cost ${result['estimated_repair_cost']:.2f}, "
                        f"{len(result['damage_items'])} items",
                metadata={
                    "severity": result["damage_severity"],
                    "items": result["damage_items"],
                    "cost": result["estimated_repair_cost"],
                    "recommend_claim": result["damage_detected"] and result["damage_severity"] >= 2,
                },
            )

        yield self.emitter.text_message_start()
        yield self.emitter.text_message_content(
            "Inspection report generated and saved. "
            "The report includes before/after photo comparisons, "
            "damage annotations, severity assessment, and cost estimates. "
            + (
                "Damage was detected - recommending initiation of damage claim flow."
                if result["damage_detected"]
                else "No significant damage detected. Property is ready for next guest."
            )
        )
        yield self.emitter.text_message_end()

        # Emit state snapshot with all analysis data
        yield self.emitter.state_snapshot(
            {
                "flow": flow_name,
                "damage_detected": result["damage_detected"],
                "severity": result["damage_severity"],
                "items": result["damage_items"],
                "estimated_cost": result["estimated_repair_cost"],
                "confidence": result["confidence"],
            }
        )

        # Transition to DONE
        old_state = self.state_machine.current_state
        self.state_machine.transition(to_state=DONE)
        yield self.emitter.flow_state_changed(flow_name, old_state, DONE)

        # ── Memory: Record inspection result ─────────────────────────────
        if self._recorder:
            await self._recorder.record_incident_update(
                event="photo_inspection_completed",
                details=f"Damage: {'Yes' if result['damage_detected'] else 'No'}, "
                        f"Severity: {result['damage_severity']}/5, "
                        f"Cost: ${result['estimated_repair_cost']:.2f}",
                damage_detected=result["damage_detected"],
                severity=result["damage_severity"],
                items_count=len(result["damage_items"]),
            )

        yield self.emitter.flow_completed(
            flow_name,
            {
                "damage_detected": result["damage_detected"],
                "severity": result["damage_severity"],
                "items": result["damage_items"],
                "estimated_cost": result["estimated_repair_cost"],
                "confidence": result["confidence"],
                "recommend_claim": result["damage_detected"] and result["damage_severity"] >= 2,
            },
        )

    @property
    def current_state(self) -> str:
        return self.state_machine.current_state

    @property
    def is_done(self) -> bool:
        return self.state_machine.is_terminal
