"""PIIMiddleware — detect and handle sensitive data.

Scans messages for PII patterns (email, phone, credit card, SSN,
passport) and applies configurable actions: redact, mask, hash,
or block.

Example::

    pii = PIIMiddleware(
        action="mask",
        patterns=["email", "phone", "credit_card"],
    )
    stack.add(pii)

Based on: LangChain PIIMiddleware concept.
"""

from __future__ import annotations

import hashlib
import logging
import re
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class PIIAction(StrEnum):
    """Action to take when PII is detected."""

    REDACT = "redact"
    MASK = "mask"
    HASH = "hash"
    BLOCK = "block"
    WARN = "warn"


# ── Pattern definitions ───────────────────────────────────────────────── #

_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    ),
    "phone": re.compile(
        r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b",
    ),
    "credit_card": re.compile(
        r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    ),
    "ssn": re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b",
    ),
    "passport": re.compile(
        r"\b[A-Z]{1,2}\d{6,9}\b",
    ),
    "ip_address": re.compile(
        r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    ),
}

_REDACTION_LABELS: dict[str, str] = {
    "email": "[EMAIL_REDACTED]",
    "phone": "[PHONE_REDACTED]",
    "credit_card": "[CC_REDACTED]",
    "ssn": "[SSN_REDACTED]",
    "passport": "[PASSPORT_REDACTED]",
    "ip_address": "[IP_REDACTED]",
}


class PIIDetection:
    """Result of PII detection in a text.

    Attributes:
        pattern_name: Which pattern matched.
        matched_text: The actual PII text found.
        start: Start position in the text.
        end: End position in the text.
    """

    __slots__ = ("pattern_name", "matched_text", "start", "end")

    def __init__(
        self,
        pattern_name: str,
        matched_text: str,
        start: int,
        end: int,
    ) -> None:
        self.pattern_name = pattern_name
        self.matched_text = matched_text
        self.start = start
        self.end = end


class PIIMiddleware:
    """Middleware for PII detection and handling.

    Scans inbound and outbound messages for PII patterns and
    applies the configured action (redact, mask, hash, block, warn).

    Args:
        action: What to do when PII is found.
        patterns: Which patterns to check (default: all).
        scan_input: Whether to scan user input messages.
        scan_output: Whether to scan model output.
    """

    def __init__(
        self,
        action: str = "redact",
        patterns: list[str] | None = None,
        scan_input: bool = True,
        scan_output: bool = True,
    ) -> None:
        self._action = PIIAction(action)
        self._patterns = patterns or list(_PATTERNS.keys())
        self._scan_input = scan_input
        self._scan_output = scan_output
        self._detection_count: int = 0

    @property
    def name(self) -> str:
        """Middleware identifier."""
        return "pii_detection"

    @property
    def detection_count(self) -> int:
        """Total number of PII instances detected."""
        return self._detection_count

    def get_tools(self) -> list[dict[str, Any]]:
        """No tools provided."""
        return []

    def get_prompt_additions(self) -> str:
        """No prompt additions."""
        return ""

    async def pre_model_call(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Scan and process PII in input messages.

        Args:
            messages: Input messages.

        Returns:
            Processed messages with PII handled.

        Raises:
            PIIBlockedError: If action is BLOCK and PII is found.
        """
        if not self._scan_input:
            return messages
        return [self._process_message(m) for m in messages]

    async def post_model_call(
        self,
        response: Any,
    ) -> Any:
        """Scan and process PII in model output.

        Args:
            response: Model response.

        Returns:
            Processed response.
        """
        if not self._scan_output:
            return response
        if hasattr(response, "content") and isinstance(response.content, str):
            response.content = self._process_text(response.content)
        return response

    def _process_message(
        self,
        message: dict[str, str],
    ) -> dict[str, str]:
        """Process a single message for PII.

        Args:
            message: Message dict with role and content.

        Returns:
            Message with PII processed.
        """
        content = message.get("content", "")
        if not content:
            return message
        processed = self._process_text(content)
        return {**message, "content": processed}

    def _process_text(self, text: str) -> str:
        """Detect and handle PII in a text string.

        Args:
            text: Text to scan.

        Returns:
            Text with PII handled per configured action.

        Raises:
            PIIBlockedError: If action is BLOCK.
        """
        detections = detect_pii(text, self._patterns)
        if not detections:
            return text

        self._detection_count += len(detections)

        if self._action == PIIAction.BLOCK:
            raise PIIBlockedError(
                f"PII detected: {len(detections)} instance(s). "
                f"Types: {_detection_types(detections)}"
            )

        if self._action == PIIAction.WARN:
            logger.warning(
                "PII detected: %d instance(s), types: %s",
                len(detections), _detection_types(detections),
            )
            return text

        return _apply_action(text, detections, self._action)


class PIIBlockedError(Exception):
    """Raised when PII is detected and action is BLOCK."""


def detect_pii(
    text: str,
    pattern_names: list[str] | None = None,
) -> list[PIIDetection]:
    """Scan text for PII patterns.

    Args:
        text: Text to scan.
        pattern_names: Which patterns to check (default: all).

    Returns:
        List of PIIDetection results, sorted by position.
    """
    names = pattern_names or list(_PATTERNS.keys())
    detections: list[PIIDetection] = []

    for name in names:
        pattern = _PATTERNS.get(name)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            detections.append(PIIDetection(
                pattern_name=name,
                matched_text=match.group(),
                start=match.start(),
                end=match.end(),
            ))

    detections.sort(key=lambda d: d.start)
    return detections


def _apply_action(
    text: str,
    detections: list[PIIDetection],
    action: PIIAction,
) -> str:
    """Apply redact/mask/hash action to detected PII.

    Processes detections in reverse order to preserve positions.

    Args:
        text: Original text.
        detections: PII detections sorted by position.
        action: Action to apply.

    Returns:
        Text with PII replaced.
    """
    result = text
    for detection in reversed(detections):
        replacement = _get_replacement(detection, action)
        result = (
            result[:detection.start]
            + replacement
            + result[detection.end:]
        )
    return result


def _get_replacement(
    detection: PIIDetection,
    action: PIIAction,
) -> str:
    """Get replacement text for a PII detection.

    Args:
        detection: PII detection to replace.
        action: Action type.

    Returns:
        Replacement string.
    """
    if action == PIIAction.REDACT:
        return _REDACTION_LABELS.get(
            detection.pattern_name, "[REDACTED]",
        )
    if action == PIIAction.MASK:
        return _mask_text(detection.matched_text)
    if action == PIIAction.HASH:
        return _hash_text(detection.matched_text)
    return detection.matched_text


def _mask_text(text: str) -> str:
    """Mask text keeping first and last 2 characters.

    Args:
        text: Text to mask.

    Returns:
        Masked text (e.g. "jo***om" for "john@example.com").
    """
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


def _hash_text(text: str) -> str:
    """Hash text with SHA-256 (first 8 chars).

    Args:
        text: Text to hash.

    Returns:
        Hash prefix (e.g. "[SHA:a1b2c3d4]").
    """
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return f"[SHA:{digest}]"


def _detection_types(detections: list[PIIDetection]) -> str:
    """Format detection types as comma-separated string.

    Args:
        detections: List of detections.

    Returns:
        Comma-separated type names.
    """
    types = sorted({d.pattern_name for d in detections})
    return ", ".join(types)
