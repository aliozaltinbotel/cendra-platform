"""Concrete :class:`MemoryPromptExtractor` implementations.

Each extractor is a thin adapter that turns one upstream memory store
into ``tuple[MemoryPrompt, ...]`` for the aggregator fan-out.  The
extractors here are the first production bridges — prior to this
module the aggregator was always instantiated with an empty extractor
tuple.

All extractors:

- Depend on narrow ``Protocol`` ports instead of concrete stores so
  they stay unit-testable without Redis / Qdrant.
- Fail open — an exception from the upstream store is logged and
  swallowed; the aggregator already records the failure via
  ``asyncio.gather(..., return_exceptions=True)``, but we also want
  extractors to degrade gracefully when one of several calls fails.
- Keep each emitted :class:`MemoryPrompt` under the 160-char limit by
  delegating truncation to the :class:`MemoryPrompt` post-init.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import structlog

from brain_engine.gestures.models import (
    GestureContext,
    MemoryPrompt,
    MemoryPromptKind,
    MemorySource,
)

__all__ = [
    "CustomerMemoryExtractor",
    "CustomerMemoryPort",
    "FactsExtractor",
    "FactsPort",
    "GuestHistoryExtractor",
    "GuestHistoryPort",
]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Customer memory
# ---------------------------------------------------------------------------


@runtime_checkable
class CustomerMemoryPort(Protocol):
    """Narrow port over :class:`CustomerMemory`.

    Only the two calls the extractor actually uses are declared so any
    fake can satisfy the port without implementing the full Redis-backed
    class.
    """

    async def get_pm_preferences(
        self,
        customer_id: str,
    ) -> dict[str, Any]:
        ...

    async def recall_events(
        self,
        customer_id: str,
        *,
        property_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[Any]:
        ...


_DEFAULT_EVENT_LIMIT: int = 5
_NEGATIVE_OUTCOMES: frozenset[str] = frozenset(
    {"failure", "override", "escalated", "lost"}
)


class CustomerMemoryExtractor:
    """Emit PM-preference + recent-event prompts from customer memory."""

    def __init__(
        self,
        memory: CustomerMemoryPort,
        *,
        event_limit: int = _DEFAULT_EVENT_LIMIT,
    ) -> None:
        if event_limit < 1:
            raise ValueError("event_limit must be >= 1")
        self._memory = memory
        self._event_limit = int(event_limit)
        self._log = logger.bind(component="customer_memory_extractor")

    async def extract(
        self,
        context: GestureContext,
    ) -> tuple[MemoryPrompt, ...]:
        """Return preference + event prompts for the active customer."""
        customer_id = context.owner_id
        if not customer_id:
            return ()
        prefs, events = await asyncio.gather(
            self._safe_prefs(customer_id),
            self._safe_events(customer_id, context.property_id),
            return_exceptions=False,
        )
        prompts: list[MemoryPrompt] = []
        prompts.extend(_prefs_to_prompts(prefs))
        prompts.extend(_events_to_prompts(events))
        return tuple(prompts)

    # ------------------------------------------------------------------
    # Defensive wrappers
    # ------------------------------------------------------------------

    async def _safe_prefs(
        self,
        customer_id: str,
    ) -> dict[str, Any]:
        try:
            return await self._memory.get_pm_preferences(customer_id)
        except Exception as exc:  # noqa: BLE001 - fail open
            self._log.warning(
                "customer_memory.prefs_failed",
                customer_id=customer_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {}

    async def _safe_events(
        self,
        customer_id: str,
        property_id: str,
    ) -> list[Any]:
        try:
            return await self._memory.recall_events(
                customer_id,
                property_id=property_id or None,
                limit=self._event_limit,
            )
        except Exception as exc:  # noqa: BLE001 - fail open
            self._log.warning(
                "customer_memory.events_failed",
                customer_id=customer_id,
                property_id=property_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prefs_to_prompts(
    prefs: dict[str, Any],
) -> list[MemoryPrompt]:
    """One PREFERENCE prompt per non-empty preference key."""
    out: list[MemoryPrompt] = []
    for key, value in prefs.items():
        text = _preference_text(key, value)
        if not text:
            continue
        out.append(
            MemoryPrompt(
                kind=MemoryPromptKind.PREFERENCE,
                source=MemorySource.CUSTOMER_MEMORY,
                text=text,
                relevance=0.7,
                reference_id=f"pref:{key}",
            )
        )
    return out


def _preference_text(key: str, value: Any) -> str:
    """Render a preference key/value pair as a short human string."""
    value_str = str(value).strip()
    if not value_str or value_str.lower() in {"none", "null", ""}:
        return ""
    label = key.replace("_", " ").strip()
    return f"PM preference — {label}: {value_str}"


def _events_to_prompts(events: list[Any]) -> list[MemoryPrompt]:
    """Most-recent events become HISTORY or WARNING prompts."""
    out: list[MemoryPrompt] = []
    for event in events:
        summary = (getattr(event, "summary", "") or "").strip()
        if not summary:
            continue
        outcome = (getattr(event, "outcome", "") or "").lower()
        kind = (
            MemoryPromptKind.WARNING
            if outcome in _NEGATIVE_OUTCOMES
            else MemoryPromptKind.HISTORY
        )
        relevance = 0.8 if kind is MemoryPromptKind.WARNING else 0.55
        reference_id = getattr(event, "event_id", None) or None
        out.append(
            MemoryPrompt(
                kind=kind,
                source=MemorySource.CUSTOMER_MEMORY,
                text=summary,
                relevance=relevance,
                reference_id=reference_id,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Guest history
# ---------------------------------------------------------------------------


@runtime_checkable
class GuestHistoryPort(Protocol):
    """Narrow port over :class:`GuestHistoryStore`."""

    async def get_guest(self, guest_id: str) -> Any | None:
        ...

    async def get_guest_incidents(
        self,
        guest_id: str,
        limit: int = 50,
    ) -> list[Any]:
        ...

    async def get_property_incidents(
        self,
        property_id: str,
        limit: int = 50,
    ) -> list[Any]:
        ...


_DEFAULT_INCIDENT_LIMIT: int = 5
_OPEN_INCIDENT_STATUS: frozenset[str] = frozenset(
    {"open", "in_progress", "escalated"}
)
_WARNING_GUEST_TAGS: frozenset[str] = frozenset(
    {"damage_prone", "high_risk", "chargeback_risk", "fraud_suspect"}
)


class GuestHistoryExtractor:
    """Emit guest profile + incident prompts from guest history."""

    def __init__(
        self,
        history: GuestHistoryPort,
        *,
        incident_limit: int = _DEFAULT_INCIDENT_LIMIT,
    ) -> None:
        if incident_limit < 1:
            raise ValueError("incident_limit must be >= 1")
        self._history = history
        self._incident_limit = int(incident_limit)
        self._log = logger.bind(component="guest_history_extractor")

    async def extract(
        self,
        context: GestureContext,
    ) -> tuple[MemoryPrompt, ...]:
        """Assemble guest + property incident prompts."""
        guest_id = context.guest_id
        property_id = context.property_id
        profile, guest_incidents, property_incidents = await asyncio.gather(
            self._safe_profile(guest_id),
            self._safe_guest_incidents(guest_id),
            self._safe_property_incidents(property_id),
            return_exceptions=False,
        )
        prompts: list[MemoryPrompt] = []
        prompts.extend(_profile_to_prompts(profile))
        prompts.extend(_incidents_to_prompts(guest_incidents, scope="guest"))
        prompts.extend(
            _incidents_to_prompts(property_incidents, scope="property"),
        )
        return tuple(prompts)

    # ------------------------------------------------------------------
    # Defensive wrappers
    # ------------------------------------------------------------------

    async def _safe_profile(self, guest_id: str | None) -> Any | None:
        if not guest_id:
            return None
        try:
            return await self._history.get_guest(guest_id)
        except Exception as exc:  # noqa: BLE001 - fail open
            self._log.warning(
                "guest_history.profile_failed",
                guest_id=guest_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def _safe_guest_incidents(
        self,
        guest_id: str | None,
    ) -> list[Any]:
        if not guest_id:
            return []
        try:
            return await self._history.get_guest_incidents(
                guest_id,
                self._incident_limit,
            )
        except Exception as exc:  # noqa: BLE001 - fail open
            self._log.warning(
                "guest_history.guest_incidents_failed",
                guest_id=guest_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    async def _safe_property_incidents(
        self,
        property_id: str,
    ) -> list[Any]:
        if not property_id:
            return []
        try:
            return await self._history.get_property_incidents(
                property_id,
                self._incident_limit,
            )
        except Exception as exc:  # noqa: BLE001 - fail open
            self._log.warning(
                "guest_history.property_incidents_failed",
                property_id=property_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []


# ---------------------------------------------------------------------------
# Guest-history helpers
# ---------------------------------------------------------------------------


def _profile_to_prompts(profile: Any | None) -> list[MemoryPrompt]:
    """Tags become WARNING or PREFERENCE; damage-claim count becomes WARNING."""
    if profile is None:
        return []
    out: list[MemoryPrompt] = []
    tags = tuple(getattr(profile, "tags", ()) or ())
    guest_id = getattr(profile, "guest_id", None) or None
    for tag in tags:
        tag_str = str(tag).strip().lower()
        if not tag_str:
            continue
        label = tag_str.replace("_", " ")
        is_warn = tag_str in _WARNING_GUEST_TAGS
        out.append(
            MemoryPrompt(
                kind=(
                    MemoryPromptKind.WARNING
                    if is_warn
                    else MemoryPromptKind.PREFERENCE
                ),
                source=MemorySource.GUEST_HISTORY,
                text=f"Guest tag — {label}",
                relevance=0.85 if is_warn else 0.6,
                reference_id=guest_id,
            )
        )
    claims = int(getattr(profile, "total_damage_claims", 0) or 0)
    if claims > 0:
        out.append(
            MemoryPrompt(
                kind=MemoryPromptKind.WARNING,
                source=MemorySource.GUEST_HISTORY,
                text=f"Guest has {claims} past damage claim(s)",
                relevance=0.9,
                reference_id=guest_id,
            )
        )
    return out


def _incidents_to_prompts(
    incidents: list[Any],
    *,
    scope: str,
) -> list[MemoryPrompt]:
    """Unresolved incidents are WARNING; closed ones become HISTORY/CONTEXT."""
    out: list[MemoryPrompt] = []
    for inc in incidents:
        text = _incident_text(inc, scope=scope)
        if not text:
            continue
        status = (getattr(inc, "status", "") or "").lower()
        damage_detected = bool(getattr(inc, "damage_detected", False))
        is_active = status in _OPEN_INCIDENT_STATUS or damage_detected
        if is_active:
            kind = MemoryPromptKind.WARNING
        elif scope == "property":
            kind = MemoryPromptKind.CONTEXT
        else:
            kind = MemoryPromptKind.HISTORY
        severity = int(getattr(inc, "severity", 1) or 1)
        base = 0.85 if is_active else 0.5
        relevance = min(1.0, base + (max(0, severity - 1)) * 0.04)
        out.append(
            MemoryPrompt(
                kind=kind,
                source=MemorySource.GUEST_HISTORY,
                text=text,
                relevance=relevance,
                reference_id=getattr(inc, "incident_id", None) or None,
            )
        )
    return out


def _incident_text(incident: Any, *, scope: str) -> str:
    """Render an incident into a compact prompt string."""
    itype = (getattr(incident, "incident_type", "") or "").strip()
    itype = itype.replace("_", " ") or "incident"
    summary = (
        getattr(incident, "resolution_summary", None)
        or getattr(incident, "damage_description", None)
        or (getattr(incident, "status", "") or "").replace("_", " ")
        or "no details"
    )
    prefix = "Property" if scope == "property" else "Guest"
    return f"{prefix} {itype}: {summary}"


# ---------------------------------------------------------------------------
# Established facts
# ---------------------------------------------------------------------------


@runtime_checkable
class FactsPort(Protocol):
    """Narrow port over :class:`FactStore`.

    Only the two read accessors are exposed — ``search`` for a scenario-
    derived query and ``get_all`` as a property-wide fallback when the
    context has no query hint.  Writes live in the nightly consolidation
    pipeline and are deliberately out of the port surface.
    """

    async def search(
        self,
        query: str,
        property_id: str = "",
        top_k: int = 10,
    ) -> list[Any]:
        ...

    async def get_all(
        self,
        property_id: str,
        limit: int = 100,
    ) -> list[Any]:
        ...


_DEFAULT_FACT_LIMIT: int = 5
_PREFERENCE_FACT_TYPES: frozenset[str] = frozenset(
    {"preference", "rule"}
)
_WARNING_FACT_TYPES: frozenset[str] = frozenset(
    {"incident", "damage", "complaint", "blocker"}
)


class FactsExtractor:
    """Emit prompts derived from established facts for a property.

    Pulls from :class:`FactStore` scoped by ``context.property_id`` and
    maps each :class:`StoredFact` to a :class:`MemoryPrompt` whose kind
    reflects the fact category (preference, rule, incident, info).

    When ``context.features['query']`` is present the extractor prefers
    vector search; otherwise it falls back to :meth:`get_all` so the
    property's baseline facts are always available on the card.
    """

    def __init__(
        self,
        facts: FactsPort,
        *,
        fact_limit: int = _DEFAULT_FACT_LIMIT,
    ) -> None:
        if fact_limit < 1:
            raise ValueError("fact_limit must be >= 1")
        self._facts = facts
        self._fact_limit = int(fact_limit)
        self._log = logger.bind(component="facts_extractor")

    async def extract(
        self,
        context: GestureContext,
    ) -> tuple[MemoryPrompt, ...]:
        property_id = context.property_id
        if not property_id:
            return ()

        query = self._query_from(context)
        if query:
            facts = await self._safe_search(query, property_id)
        else:
            facts = await self._safe_get_all(property_id)

        return tuple(_facts_to_prompts(facts))

    # ── Private ─────────────────────────────────────────────── #

    def _query_from(self, context: GestureContext) -> str:
        """Derive an optional semantic query from context features."""
        raw = context.features.get("query") if context.features else None
        if not isinstance(raw, str):
            return ""
        return raw.strip()

    async def _safe_search(
        self,
        query: str,
        property_id: str,
    ) -> list[Any]:
        try:
            return await self._facts.search(
                query,
                property_id=property_id,
                top_k=self._fact_limit,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "facts_search_failed",
                property_id=property_id,
                error=str(exc),
            )
            return []

    async def _safe_get_all(
        self,
        property_id: str,
    ) -> list[Any]:
        try:
            return await self._facts.get_all(
                property_id,
                limit=self._fact_limit,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "facts_get_all_failed",
                property_id=property_id,
                error=str(exc),
            )
            return []


def _facts_to_prompts(
    facts: list[Any],
) -> list[MemoryPrompt]:
    out: list[MemoryPrompt] = []
    for fact in facts:
        content = (getattr(fact, "content", "") or "").strip()
        if not content:
            continue
        kind = _kind_for_fact(fact)
        relevance = _relevance_for_fact(fact, kind)
        out.append(
            MemoryPrompt(
                kind=kind,
                source=MemorySource.FACTS,
                text=content,
                relevance=relevance,
                reference_id=getattr(fact, "fact_id", None) or None,
            )
        )
    return out


def _kind_for_fact(fact: Any) -> MemoryPromptKind:
    """Translate ``StoredFact.fact_type`` into a prompt kind."""
    raw = (getattr(fact, "fact_type", "") or "").strip().lower()
    if raw in _WARNING_FACT_TYPES:
        return MemoryPromptKind.WARNING
    if raw in _PREFERENCE_FACT_TYPES:
        return MemoryPromptKind.PREFERENCE
    return MemoryPromptKind.CONTEXT


def _relevance_for_fact(
    fact: Any,
    kind: MemoryPromptKind,
) -> float:
    """Blend confidence with the prompt kind to rank facts.

    Warnings earn a small kind-bonus so an incident fact outranks a
    generic preference at equal confidence; the final value is clamped
    by :class:`MemoryPrompt` itself, but we cap here to keep behaviour
    explicit.
    """
    confidence = _as_float(getattr(fact, "confidence", 1.0), default=1.0)
    base = max(0.0, min(1.0, confidence))
    bonus = 0.1 if kind is MemoryPromptKind.WARNING else 0.0
    return min(1.0, base + bonus)


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
