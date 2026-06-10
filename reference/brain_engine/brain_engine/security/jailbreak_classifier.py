"""Jailbreak classifier — known-family detection.

Reference: ``brain_engine_advisory.md`` §9.3 — jailbreak classifier.

The companion :mod:`brain_engine.security.prompt_injection` module
catches *generic* injection (ignore-previous, role-rewrite, system-tag
smuggling).  This module is narrower: it scores how closely the input
matches one of the **named jailbreak families** circulating in public
corpora (DAN, "developer mode", grandma exploit, base64 payloads, …).

Both modules can fire on the same input — they answer different
questions.  Injection asks *"is this an attack?"*; the jailbreak
classifier asks *"is this a known attack template?"*.  The cascade
combines both verdicts when deciding to soft-block, hard-block, or
escalate to human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Pattern


class JailbreakFamily(str, Enum):
    """Named families with a concrete fingerprint."""

    DAN = "dan"                      # "Do Anything Now"
    DEV_MODE = "dev_mode"            # "developer mode enabled"
    ROLEPLAY_OVERRIDE = "roleplay_override"
    GRANDMA = "grandma_exploit"      # "my grandma used to read me ..."
    SYSTEM_LEAK = "system_leak"      # "print your initial prompt"
    ENCODED_PAYLOAD = "encoded_payload"
    INSTRUCTION_HIJACK = "instruction_hijack"


@dataclass(frozen=True, slots=True)
class JailbreakVerdict:
    """Outcome of a single :meth:`JailbreakClassifier.classify` call."""

    matched: tuple[JailbreakFamily, ...]
    score: float
    rationale: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return not self.matched


# ── Rule registry ───────────────────────────────────────────────────
# Each entry: (family, pattern, weight, rationale)

_RULES: tuple[
    tuple[JailbreakFamily, Pattern[str], float, str], ...
] = (
    (
        JailbreakFamily.DAN,
        re.compile(
            r"\b(do[\s-]anything[\s-]now|DAN\s+mode|\[DAN\])",
            re.IGNORECASE,
        ),
        0.95,
        "DAN-family marker",
    ),
    (
        JailbreakFamily.DEV_MODE,
        re.compile(
            r"\b(developer\s+mode\s+enabled|enable\s+dev(?:eloper)?\s+mode)",
            re.IGNORECASE,
        ),
        0.9,
        "Developer-mode toggle",
    ),
    (
        JailbreakFamily.ROLEPLAY_OVERRIDE,
        re.compile(
            r"\b(pretend\s+(?:to\s+be|you\s+are)|"
            r"act\s+as\s+(?:if\s+you\s+were\s+)?an?\s+\w+|"
            r"role[\s-]?play\s+as)",
            re.IGNORECASE,
        ),
        0.6,
        "Role-play override",
    ),
    (
        JailbreakFamily.GRANDMA,
        re.compile(
            r"\bmy\s+(?:grand)?ma\s+used\s+to",
            re.IGNORECASE,
        ),
        0.85,
        "Grandma-exploit framing",
    ),
    (
        JailbreakFamily.SYSTEM_LEAK,
        re.compile(
            r"\b(print|repeat|reveal|show)\s+(?:your\s+)?"
            r"(?:initial\s+|system\s+|hidden\s+)?(?:prompt|instructions)",
            re.IGNORECASE,
        ),
        0.85,
        "System-prompt leak attempt",
    ),
    (
        JailbreakFamily.INSTRUCTION_HIJACK,
        re.compile(
            r"\b(forget\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
            r"new\s+instructions:\s)",
            re.IGNORECASE,
        ),
        0.9,
        "Instruction hijack",
    ),
)


# Base64-shape detector — long contiguous A-Z/a-z/0-9/+/= runs are a
# strong heuristic for an encoded payload smuggled inside chat text.
# Pure plain text rarely has > 40-char runs without spaces.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{60,}")


class JailbreakClassifier:
    """Score input against known jailbreak families.

    The classifier is stateless and deterministic; the same input
    always returns the same verdict, so callers can cache by content
    hash if a single message is scored more than once per turn.
    """

    def classify(self, text: str) -> JailbreakVerdict:
        if not text:
            return JailbreakVerdict(
                matched=(), score=0.0, rationale=(),
            )
        matched: list[JailbreakFamily] = []
        rationale: list[str] = []
        weights: list[float] = []
        for family, pattern, weight, why in _RULES:
            if pattern.search(text):
                matched.append(family)
                rationale.append(why)
                weights.append(weight)
        if _BASE64_RUN.search(text):
            matched.append(JailbreakFamily.ENCODED_PAYLOAD)
            rationale.append("Long base64-shaped run")
            weights.append(0.7)
        return JailbreakVerdict(
            matched=tuple(matched),
            score=self._aggregate(weights),
            rationale=tuple(rationale),
        )

    @staticmethod
    def _aggregate(weights: list[float]) -> float:
        """Combine independent weights into [0, 1] confidence.

        Uses the standard "noisy-or" formula — each weight is the
        probability the family is present, and we assume independence.
        """
        prob_clean = 1.0
        for w in weights:
            prob_clean *= max(0.0, 1.0 - w)
        return round(1.0 - prob_clean, 4)
