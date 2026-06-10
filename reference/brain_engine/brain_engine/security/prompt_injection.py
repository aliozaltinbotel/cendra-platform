"""Rule-based prompt-injection detector.

The cascade (ADR-0005) decides what to do with a verdict; this module
only *labels* candidate input.  We deliberately avoid an LLM-based
detector here — the request path budget (advisory §5 SLOs) cannot
absorb another model call, and a deterministic rule is auditable.

Three signal sources, evaluated in cost order:

1. **Imperative jailbreak templates** — short fixed strings copied
   from public jailbreak corpora ("ignore previous", "you are now").
2. **Role-rewrite attempts** — the input claims to redefine the
   assistant or system role.
3. **Instruction smuggling via formatting** — markdown / XML-ish
   tags that mimic system prompts ("<|system|>", "[INST]", "###").

The detector returns a verdict with a confidence score and the
matched rule ids; downstream code can choose to log, soft-block,
or hard-block depending on tenant policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Pattern


class InjectionSeverity(str, Enum):
    """How aggressive the candidate looks."""

    BENIGN = "benign"
    SUSPICIOUS = "suspicious"
    HOSTILE = "hostile"


@dataclass(frozen=True, slots=True)
class InjectionVerdict:
    """Outcome of a single ``classify`` call."""

    severity: InjectionSeverity
    confidence: float
    matched_rules: tuple[str, ...]


# ── Rule registry ───────────────────────────────────────────────────
# Each entry: (rule_id, severity_on_match, pattern, weight)
_RULES: tuple[tuple[str, InjectionSeverity, Pattern[str], float], ...] = (
    (
        "ignore_previous",
        InjectionSeverity.HOSTILE,
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+"
            r"(?:all|previous|prior|above)\s+"
            r"(?:instructions?|prompts?|rules?|directives?)",
            re.IGNORECASE,
        ),
        0.9,
    ),
    (
        "role_rewrite",
        InjectionSeverity.HOSTILE,
        re.compile(
            r"\byou\s+are\s+now\b|"
            r"\bact\s+as\s+(?:a\s+)?(?:dan|jailbroken|"
            r"unrestricted|root|admin)\b",
            re.IGNORECASE,
        ),
        0.85,
    ),
    (
        "system_tag_smuggle",
        InjectionSeverity.HOSTILE,
        re.compile(
            r"<\|system\|>|<\|im_start\|>|\[INST\]|"
            r"###\s*system\b",
            re.IGNORECASE,
        ),
        0.95,
    ),
    (
        "secret_exfil",
        InjectionSeverity.HOSTILE,
        re.compile(
            r"\b(?:show|reveal|print|leak|dump)\s+"
            r"(?:the\s+)?(?:system\s+)?(?:prompt|instructions?|"
            r"api[_\s-]?key|secret|token)s?\b",
            re.IGNORECASE,
        ),
        0.85,
    ),
    (
        "policy_bypass",
        InjectionSeverity.SUSPICIOUS,
        re.compile(
            r"\bbypass\s+(?:the\s+)?"
            r"(?:safety|guardrail|policy|filter)s?\b|"
            r"\bdeveloper\s+mode\b",
            re.IGNORECASE,
        ),
        0.6,
    ),
    (
        "unicode_obfuscation",
        InjectionSeverity.SUSPICIOUS,
        # Common zero-width / RTL override characters
        re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069]"),
        0.5,
    ),
)


class PromptInjectionDetector:
    """Stateless classifier; thread-safe by construction."""

    def classify(self, text: str) -> InjectionVerdict:
        if not text:
            return InjectionVerdict(
                severity=InjectionSeverity.BENIGN,
                confidence=0.0,
                matched_rules=(),
            )
        matched: list[tuple[str, InjectionSeverity, float]] = []
        for rule_id, severity, pattern, weight in _RULES:
            if pattern.search(text):
                matched.append((rule_id, severity, weight))
        if not matched:
            return InjectionVerdict(
                severity=InjectionSeverity.BENIGN,
                confidence=0.0,
                matched_rules=(),
            )
        # Severity = worst matched severity.
        severity = self._worst_severity(
            [m[1] for m in matched],
        )
        # Confidence = 1 − product of (1 − weight) over matches,
        # clamped to [0.0, 1.0].  This grows with the number and
        # weight of matches without ever exceeding 1.0.
        miss = 1.0
        for _, _, weight in matched:
            miss *= 1.0 - weight
        confidence = max(0.0, min(1.0, 1.0 - miss))
        return InjectionVerdict(
            severity=severity,
            confidence=confidence,
            matched_rules=tuple(rule_id for rule_id, _, _ in matched),
        )

    @staticmethod
    def _worst_severity(
        severities: list[InjectionSeverity],
    ) -> InjectionSeverity:
        if InjectionSeverity.HOSTILE in severities:
            return InjectionSeverity.HOSTILE
        if InjectionSeverity.SUSPICIOUS in severities:
            return InjectionSeverity.SUSPICIOUS
        return InjectionSeverity.BENIGN
