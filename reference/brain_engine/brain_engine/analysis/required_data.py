"""Foundation Layer required_data presence gate (Q5-B).

The reactive foundation catalog tags every scenario with a tuple
of ``required_data_checks`` labels — short human strings that
name the data points the orchestrator needs in order to apply
the scenario safely (e.g. ``"cleaner schedule"``, ``"pms
reservation"``, ``"arrival time"``).  Until Sprint 6 those labels
were only consumed by the rule-creation discovery path
(:mod:`brain_engine.analysis.iterative_questioning`); the live
guest path had no visibility into whether the data the
foundation said was required actually reached the orchestrator.

This module closes that gap.  Each catalog label is mapped to
one of the four typed snapshot buckets that already live on
:class:`brain_engine.patterns.models.DecisionCase` and on the
extended :class:`brain_engine.analysis.models.AnalysisEvent`:
``pms_snapshot``, ``calendar_snapshot``, ``ops_snapshot``, and
``guest_snapshot``.  The orchestrator's
:meth:`~brain_engine.analysis.orchestrator.FoundationAnalysisOrchestrator._validate_required_data`
step walks the dominant catalog entry's checks, classifies each
into its bucket, and reports the labels whose target bucket is
empty on the event.

Scope (Q5-B Variant 1):

* The mapping covers the **40 mappable labels** identified by the
  2026-05-18 audit script (53 % of the 76 unique catalog labels).
  Those are the labels whose semantics match a real PMS / ops /
  calendar / guest data field.
* The remaining **36 labels** are knowledge / policy references
  (``"house rules"``, ``"property sop"``, ``"compensation
  policy"``, ``"owner preferences"``, …) that belong on the
  memory-tier / RAG path, not on the per-event snapshot path.
  Those are intentionally returned as :data:`UNMAPPED` so the
  orchestrator can log them without gating.  A follow-up Q5-B.2
  PR will wire them to the existing
  :class:`brain_engine.analysis.models.MemoryTier` slugs once we
  have observation data showing which ones matter in production.

The mapping is deliberately conservative: when in doubt a label
maps to ``UNMAPPED`` so the gate fails open instead of producing
a false positive.  Adding a new mapping is a one-line dict
addition + a test row in
``tests/test_required_data_mapping.py``.

The module is pure compute: no I/O, no LLM calls, no globals
beyond the frozen mapping table.  Idempotent and thread-safe.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

__all__ = [
    "REQUIRED_DATA_SNAPSHOTS",
    "UNMAPPED",
    "classify_required_data_check",
    "find_missing_required_data",
]


# Sentinel returned by :func:`classify_required_data_check` for
# labels that have no clean event-snapshot equivalent.  Public so
# tests + callers can compare against it without re-importing the
# string literal.
UNMAPPED: Final[str] = "UNMAPPED"


# The four typed snapshot buckets already carried on
# :class:`brain_engine.patterns.models.DecisionCase` and on
# :class:`brain_engine.analysis.models.AnalysisEvent`.  Kept as a
# frozen tuple so callers can iterate the canonical set without
# importing the dataclass.
REQUIRED_DATA_SNAPSHOTS: Final[tuple[str, ...]] = (
    "pms_snapshot",
    "calendar_snapshot",
    "ops_snapshot",
    "guest_snapshot",
)


# Verbatim catalog label (lowercased, whitespace-stripped) →
# snapshot bucket.  Built from the 2026-05-18 mapping audit
# against the live foundation catalog (76 unique labels across
# 469 scenarios; 40 mappable to a snapshot bucket, the other 36
# documented in the module docstring as Q5-B.2 candidates).
#
# Lookup order in :func:`classify_required_data_check`: exact
# match here first; anything else returns :data:`UNMAPPED`.
_REQUIRED_DATA_MAPPING: Final[Mapping[str, str]] = {
    # ── pms_snapshot (17 labels) ──
    "pms reservation": "pms_snapshot",
    "channel policy": "pms_snapshot",
    "reservation status": "pms_snapshot",
    "reservation identity": "pms_snapshot",
    "security deposit": "pms_snapshot",
    "payment status": "pms_snapshot",
    "channel fees": "pms_snapshot",
    "listing data": "pms_snapshot",
    "amenity list": "pms_snapshot",
    "amenities": "pms_snapshot",
    "property type": "pms_snapshot",
    "property status": "pms_snapshot",
    "property address": "pms_snapshot",
    "property policies": "pms_snapshot",
    "property rules": "pms_snapshot",
    "unit number": "pms_snapshot",
    "currency": "pms_snapshot",
    # ── calendar_snapshot (10 labels) ──
    "arrival time": "calendar_snapshot",
    "departure time": "calendar_snapshot",
    "check-in time": "calendar_snapshot",
    "check-out time": "calendar_snapshot",
    "stay dates": "calendar_snapshot",
    "calendar availability": "calendar_snapshot",
    "lead time": "calendar_snapshot",
    "next reservation": "calendar_snapshot",
    "previous reservation": "calendar_snapshot",
    "turnover window": "calendar_snapshot",
    # ── ops_snapshot (9 labels) ──
    "cleaning schedule": "ops_snapshot",
    "vendor availability": "ops_snapshot",
    "housekeeping readiness": "ops_snapshot",
    "vendor schedule": "ops_snapshot",
    "maintenance schedule": "ops_snapshot",
    "access-code status": "ops_snapshot",
    "lockbox status": "ops_snapshot",
    "smart lock status": "ops_snapshot",
    "battery status": "ops_snapshot",
    # ── guest_snapshot (4 labels) ──
    "guest history": "guest_snapshot",
    "guest profile": "guest_snapshot",
    "guest review history": "guest_snapshot",
    "guest verification": "guest_snapshot",
}


def classify_required_data_check(label: str) -> str:
    """Return the snapshot bucket name for ``label`` or :data:`UNMAPPED`.

    Args:
        label: One ``required_data_checks`` entry from a foundation
            scenario (verbatim, e.g. ``"PMS Reservation"`` or
            ``"  cleaning schedule  "``).

    Returns:
        One of the strings in :data:`REQUIRED_DATA_SNAPSHOTS`, or
        :data:`UNMAPPED` when the label has no event-snapshot
        equivalent (knowledge / policy / memory-tier categories).

    The lookup is case-insensitive and whitespace-tolerant so a
    catalog edit that flips capitalisation or pads spacing does
    not silently drop the mapping.  Empty / blank strings return
    :data:`UNMAPPED` (a missing label cannot be classified).
    """
    if not label or not label.strip():
        return UNMAPPED
    normalised = label.strip().lower()
    return _REQUIRED_DATA_MAPPING.get(normalised, UNMAPPED)


def find_missing_required_data(
    required_checks: tuple[str, ...],
    snapshots: Mapping[str, Mapping[str, object]],
) -> tuple[str, ...]:
    """Return the catalog labels whose target snapshot is empty.

    Walks ``required_checks`` (the foundation scenario's
    ``required_data_checks`` tuple), classifies each one with
    :func:`classify_required_data_check`, and reports the labels
    whose target snapshot is missing or empty in ``snapshots``.

    Unmapped labels are intentionally skipped — they belong on
    the memory-tier path the Q5-B.2 follow-up will wire.  The
    orchestrator should log them separately for observability but
    must not gate on them in Q5-B Variant 1, because doing so
    would block ~half of all catalog entries on every event
    until the knowledge-tier integration lands.

    Args:
        required_checks: The dominant catalog entry's
            ``required_data_checks`` tuple.  May be empty (no
            checks defined ⇒ nothing to validate, returns ``()``).
        snapshots: Mapping from snapshot bucket name (one of
            :data:`REQUIRED_DATA_SNAPSHOTS`) to the actual
            snapshot dict carried on the
            :class:`brain_engine.analysis.models.AnalysisEvent`.
            A missing key is treated as an empty snapshot.

    Returns:
        Tuple of the original (verbatim) catalog labels whose
        target snapshot is empty.  Order matches
        ``required_checks``; duplicates dedup; case-insensitively
        identical labels collapse to the first occurrence.
    """
    if not required_checks:
        return ()
    missing: list[str] = []
    seen: set[str] = set()
    for check in required_checks:
        normalised = check.strip()
        if not normalised:
            continue
        key = normalised.lower()
        if key in seen:
            continue
        seen.add(key)
        target = classify_required_data_check(normalised)
        if target == UNMAPPED:
            continue
        snapshot = snapshots.get(target) or {}
        if not snapshot:
            missing.append(normalised)
    return tuple(missing)
