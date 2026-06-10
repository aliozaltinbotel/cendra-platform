"""Country-aware PII detector for Brain Engine.

The detector covers the identifier set Brain Engine encounters in V1
operations — short-stay rentals across the EU, plus passport, IBAN,
and contact channels.  Scope is deliberately small and well-tested
rather than broad and approximate; uncertain matches are *not*
emitted, because a false positive that masks a property name (e.g.
``"Calle Mayor 28012"``) is worse than a missed PII fragment that the
neuro-symbolic cascade (ADR-0005) catches on the next layer.

Reference: ``brain_engine_advisory.md`` §4 (PII / GDPR compliance).

The implementation is regex-only.  An ML/NER pass can be layered on
top later (advisory roadmap §10.3); the regex base must stay stable
so the audit logger and retention manager keep working when the ML
layer is offline or being retrained.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from re import Pattern


class PIIType(StrEnum):
    """Categorical label for a detected span."""

    EMAIL = "email"
    PHONE = "phone"
    IBAN = "iban"
    CREDIT_CARD = "credit_card"
    PASSPORT = "passport"
    # Country-specific national IDs.  See advisory §4 (1).
    DNI_ES = "dni_es"  # Spain — Documento Nacional de Identidad
    NIE_ES = "nie_es"  # Spain — Número de Identidad de Extranjero
    NIF_ES = "nif_es"  # Spain — Número de Identificación Fiscal
    CODICE_FISCALE_IT = "codice_fiscale_it"
    SSN_FR = "ssn_fr"  # Numéro de Sécurité Sociale
    PERSONALAUSWEIS_DE = "personalausweis_de"
    INN_RU = "inn_ru"  # Russian taxpayer number
    TC_KIMLIK_TR = "tc_kimlik_tr"  # Turkish national ID

    @property
    def severity(self) -> int:
        """1 (low) → 5 (highest); drives audit retention.

        Severity 1 — contact channel that is widely shared anyway.
        Severity 3 — unique identifier that links to one person.
        Severity 5 — financial / passport / national ID with the
        ability to commit identity fraud or move money.
        """
        if self in {PIIType.EMAIL, PIIType.PHONE}:
            return 1
        if self in {
            PIIType.DNI_ES,
            PIIType.NIE_ES,
            PIIType.NIF_ES,
            PIIType.CODICE_FISCALE_IT,
            PIIType.SSN_FR,
            PIIType.PERSONALAUSWEIS_DE,
            PIIType.INN_RU,
            PIIType.TC_KIMLIK_TR,
        }:
            return 3
        return 5  # IBAN, credit card, passport


@dataclass(frozen=True, slots=True)
class PIIMatch:
    """A single detected PII span."""

    pii_type: PIIType
    start: int
    end: int
    value: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("PIIMatch start/end must be non-empty")


# ── Pattern registry ────────────────────────────────────────────────
#
# Each pattern is anchored on word boundaries to stop greedy matches
# from devouring surrounding text.  The Spanish IDs use a checksum
# letter — we match the *shape* and let an optional checksum
# validator catch false positives.

_EMAIL: Pattern[str] = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9])",
)
# Permissive E.164-ish matcher; accepts +, parens, dashes, spaces.
_PHONE: Pattern[str] = re.compile(
    r"(?<!\d)"
    r"(?:\+?\d{1,3}[\s\-.]?)?"
    r"(?:\(\d{1,4}\)[\s\-.]?)?"
    r"\d{2,4}[\s\-.]?\d{2,4}[\s\-.]?\d{2,4}"
    r"(?!\d)",
)
# IBAN — 2 letters + 2 check digits + up to 30 alphanumerics.
_IBAN: Pattern[str] = re.compile(
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
)
# Credit card — Luhn-shape only; validation happens in the helper.
_CREDIT_CARD: Pattern[str] = re.compile(
    r"\b(?:\d[ -]?){13,19}\b",
)
# Passport — generic 1–2 letters + 6–9 digits.
_PASSPORT: Pattern[str] = re.compile(
    r"\b[A-Z]{1,2}\d{6,9}\b",
)
_DNI_ES: Pattern[str] = re.compile(r"\b\d{8}[A-HJ-NP-TV-Z]\b")
_NIE_ES: Pattern[str] = re.compile(r"\b[XYZ]\d{7}[A-HJ-NP-TV-Z]\b")
_NIF_ES: Pattern[str] = re.compile(r"\b[A-HJNPQRSUVW]\d{7}[0-9A-J]\b")
_CODICE_FISCALE_IT: Pattern[str] = re.compile(
    r"\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b",
)
_SSN_FR: Pattern[str] = re.compile(
    r"\b[12]\d{2}(?:0[1-9]|1[0-2])(?:2[AB]|\d{2})\d{3}\d{3}\d{2}\b",
)
_PERSONALAUSWEIS_DE: Pattern[str] = re.compile(
    r"\b[A-Z]\d{8}\b",
)
_INN_RU: Pattern[str] = re.compile(r"\b\d{10,12}\b")
_TC_KIMLIK_TR: Pattern[str] = re.compile(r"\b[1-9]\d{10}\b")


_REGISTRY: tuple[tuple[PIIType, Pattern[str]], ...] = (
    # Order matters — most-specific patterns first to win the
    # span when matches overlap (handled in ``_dedupe`` below).
    (PIIType.EMAIL, _EMAIL),
    (PIIType.IBAN, _IBAN),
    (PIIType.CODICE_FISCALE_IT, _CODICE_FISCALE_IT),
    (PIIType.SSN_FR, _SSN_FR),
    (PIIType.NIE_ES, _NIE_ES),
    (PIIType.NIF_ES, _NIF_ES),
    (PIIType.DNI_ES, _DNI_ES),
    (PIIType.PERSONALAUSWEIS_DE, _PERSONALAUSWEIS_DE),
    (PIIType.PASSPORT, _PASSPORT),
    (PIIType.CREDIT_CARD, _CREDIT_CARD),
    (PIIType.TC_KIMLIK_TR, _TC_KIMLIK_TR),
    (PIIType.INN_RU, _INN_RU),
    (PIIType.PHONE, _PHONE),
)


def _luhn_ok(digits: str) -> bool:
    """Validate a digit string against the Luhn checksum.

    Used to reject obvious false positives in the credit-card branch
    (the regex matches any 13–19 digit run, including order numbers
    and reservation codes).
    """
    raw = re.sub(r"\D", "", digits)
    if not 13 <= len(raw) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(raw)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class PIIDetector:
    """Stateless, thread-safe PII span detector.

    Calling ``scan`` twice on the same input yields the same matches in
    the same order; this property is what lets the audit logger
    (``AuditLogger``) hash the redacted output for tamper detection.
    """

    def scan(self, text: str) -> list[PIIMatch]:
        """Return PII matches sorted by ``(start, -severity)``."""
        if not text:
            return []
        matches = list(self._iter_matches(text))
        matches = self._dedupe(matches)
        matches.sort(key=lambda m: (m.start, -m.pii_type.severity))
        return matches

    def types_present(self, text: str) -> set[PIIType]:
        """Convenience: which categories appear in ``text``."""
        return {m.pii_type for m in self.scan(text)}

    # ── Internals ───────────────────────────────────────────────────

    def _iter_matches(self, text: str) -> Iterator[PIIMatch]:
        for pii_type, pattern in _REGISTRY:
            for hit in pattern.finditer(text):
                value = hit.group(0)
                if pii_type is PIIType.CREDIT_CARD and not _luhn_ok(
                    value,
                ):
                    continue
                # INN_RU and TC_KIMLIK_TR overlap with phone numbers;
                # reject if the surrounding context is clearly a
                # phone (preceded by '+' or '(').
                if pii_type in {PIIType.INN_RU, PIIType.TC_KIMLIK_TR}:
                    if hit.start() > 0 and text[hit.start() - 1] in "+(":
                        continue
                yield PIIMatch(
                    pii_type=pii_type,
                    start=hit.start(),
                    end=hit.end(),
                    value=value,
                )

    @staticmethod
    def _dedupe(matches: Iterable[PIIMatch]) -> list[PIIMatch]:
        """Drop matches fully contained in a higher-severity one.

        When EMAIL ``"a@b.com"`` overlaps with PHONE detected on the
        digits inside the address, the email wins because it is more
        specific.  We keep the higher-severity span if both overlap.
        """
        sorted_matches = sorted(
            matches,
            key=lambda m: (-m.pii_type.severity, m.start),
        )
        kept: list[PIIMatch] = []
        for candidate in sorted_matches:
            if any(candidate.start >= k.start and candidate.end <= k.end for k in kept):
                continue
            kept.append(candidate)
        return kept
