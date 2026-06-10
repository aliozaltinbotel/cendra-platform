"""Runtime guard that turns staticity classifications into yes/no decisions.

:class:`StaticityClassifier` answers "how volatile is this field?".  The
guard answers the operational question that follows: "given a cached
value of this age, may I send it?" — and refuses to memoize anything
classified as :pyattr:`StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY`.

The guard is pure (no I/O, no global state).  Callers feed it the age
of any cached value they already have and act on the verdict:

- :pyattr:`VerdictKind.ALLOW_CACHED` — safe to send the cached value.
- :pyattr:`VerdictKind.REQUIRE_REFRESH` — value exists but is past its
  verification interval; refetch from source-of-truth, then send.
- :pyattr:`VerdictKind.REQUIRE_LIVE_FETCH` — never trust cache for this
  field (calendar, payment status, …); always refetch.
- :pyattr:`VerdictKind.REFUSE_TO_CACHE` — terminal: secret field with a
  cached copy in hand.  The guard refuses to use it; the caller MUST
  drop the cache and refetch live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

from brain_engine.staticity.classifier import (
    FieldStaticity,
    StaticityClassifier,
    StaticityLevel,
)


__all__ = [
    "AgeLookup",
    "StaticityGuard",
    "StaticityVerdict",
    "VerdictKind",
    "guard_payload",
]


logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class VerdictKind(StrEnum):
    """Operational outcome the guard returns for one field."""

    ALLOW_CACHED = "allow_cached"
    REQUIRE_REFRESH = "require_refresh"
    REQUIRE_LIVE_FETCH = "require_live_fetch"
    REFUSE_TO_CACHE = "refuse_to_cache"


@dataclass(frozen=True, slots=True)
class StaticityVerdict:
    """Result of running the guard on one field.

    Attributes:
        kind: The operational decision.
        field: The classification the verdict was derived from.
        reason: Human-readable explanation suitable for audit logs and
            UI tooltips.  Stable wire-strings — UI may switch on it.
    """

    kind: VerdictKind
    field: FieldStaticity
    reason: str

    @property
    def can_send_cached(self) -> bool:
        """Whether the caller may use the cached value as-is."""
        return self.kind is VerdictKind.ALLOW_CACHED

    @property
    def must_refetch(self) -> bool:
        """Whether a live fetch is required before sending."""
        return self.kind in {
            VerdictKind.REQUIRE_REFRESH,
            VerdictKind.REQUIRE_LIVE_FETCH,
            VerdictKind.REFUSE_TO_CACHE,
        }

    @property
    def is_secret_breach(self) -> bool:
        """Whether the verdict signals a secret was found in the cache."""
        return self.kind is VerdictKind.REFUSE_TO_CACHE


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


AgeLookup = Callable[[str], float | None]
"""Callable returning the age (seconds) of a cached field, or ``None``."""


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class StaticityGuard:
    """Pure decision function over :class:`StaticityClassifier` output.

    The guard owns no state — it delegates classification to the
    injected classifier and reports a verdict per call.  Construction
    is cheap; instantiate per request when convenient.
    """

    def __init__(self, *, classifier: StaticityClassifier) -> None:
        self._classifier = classifier
        self._log = logger.bind(component="staticity_guard")

    def evaluate(
        self,
        *,
        field_name: str,
        property_id: str,
        cached_age_seconds: float | None,
    ) -> StaticityVerdict:
        """Decide whether a cached value of the given age may be used.

        Args:
            field_name: Field being read (e.g. ``"access_code"``).
            property_id: Property the read targets.
            cached_age_seconds: Age of the cached value the caller is
                holding, or ``None`` when no cache hit is available.

        Returns:
            A :class:`StaticityVerdict` describing the decision.
        """
        classification = self._classifier.classify(
            field_name, property_id,
        )
        verdict = self._verdict_for(classification, cached_age_seconds)
        self._emit_audit(verdict, property_id, cached_age_seconds)
        return verdict

    # ── Helpers ──────────────────────────────────────────── #

    def _verdict_for(
        self,
        classification: FieldStaticity,
        cached_age_seconds: float | None,
    ) -> StaticityVerdict:
        level = classification.level
        if level is StaticityLevel.SECRET_DYNAMIC_FETCH_ONLY:
            if cached_age_seconds is not None:
                return StaticityVerdict(
                    kind=VerdictKind.REFUSE_TO_CACHE,
                    field=classification,
                    reason="secret_field_found_in_cache",
                )
            return StaticityVerdict(
                kind=VerdictKind.REQUIRE_LIVE_FETCH,
                field=classification,
                reason="secret_field_requires_live_fetch",
            )
        if level is StaticityLevel.DYNAMIC_FETCH_LIVE:
            return StaticityVerdict(
                kind=VerdictKind.REQUIRE_LIVE_FETCH,
                field=classification,
                reason="dynamic_field_requires_live_fetch",
            )
        if level is StaticityLevel.STATIC_VERIFY_PERIODICALLY:
            interval_seconds = (
                classification.verify_interval_hours * 3600.0
            )
            if (
                cached_age_seconds is None
                or cached_age_seconds > interval_seconds
            ):
                return StaticityVerdict(
                    kind=VerdictKind.REQUIRE_REFRESH,
                    field=classification,
                    reason="cache_age_exceeds_verify_interval",
                )
            return StaticityVerdict(
                kind=VerdictKind.ALLOW_CACHED,
                field=classification,
                reason="cache_within_verify_interval",
            )
        # STATIC_SAFE — immutable, always cacheable.
        return StaticityVerdict(
            kind=VerdictKind.ALLOW_CACHED,
            field=classification,
            reason="static_field_safe_to_cache",
        )

    def _emit_audit(
        self,
        verdict: StaticityVerdict,
        property_id: str,
        cached_age_seconds: float | None,
    ) -> None:
        # Secret breaches are loud (warning); routine ALLOW_CACHED stays
        # debug to avoid log floods on the hot path.
        if verdict.is_secret_breach:
            self._log.warning(
                "secret_field_cache_refused",
                field=verdict.field.field_name,
                property_id=property_id,
                cached_age_seconds=cached_age_seconds,
            )
            return
        self._log.debug(
            "staticity_verdict",
            field=verdict.field.field_name,
            property_id=property_id,
            kind=verdict.kind.value,
            reason=verdict.reason,
        )


# ---------------------------------------------------------------------------
# Payload fan-out helper
# ---------------------------------------------------------------------------


def guard_payload(
    *,
    payload: Mapping[str, Any],
    property_id: str,
    guard: StaticityGuard,
    age_lookup: AgeLookup | None = None,
    skip_keys: Iterable[str] = (),
) -> tuple[StaticityVerdict, ...]:
    """Run the guard over every key in an outbound action payload.

    Returns one :class:`StaticityVerdict` per non-skipped key in
    ``payload`` order.  Callers typically iterate the verdicts to build
    a single composite decision (e.g. block the send when any verdict
    is :pyattr:`VerdictKind.REFUSE_TO_CACHE`).

    Args:
        payload: Field → value mapping the caller is about to send.
        property_id: Property the payload targets.
        guard: The guard instance to evaluate against.
        age_lookup: Optional callable that returns the cached age for a
            field name (None when the caller is about to send a freshly
            fetched value).  When omitted, every field is treated as
            "no cache hit" so the verdict reflects the field's intrinsic
            policy (live-fetch / refresh) without secret-breach noise.
        skip_keys: Field names to ignore (e.g. metadata keys that aren't
            domain facts).

    Returns:
        Tuple of verdicts in payload-iteration order.
    """
    skipped = frozenset(skip_keys)
    lookup = age_lookup or _no_cache_age
    return tuple(
        guard.evaluate(
            field_name=name,
            property_id=property_id,
            cached_age_seconds=lookup(name),
        )
        for name in payload
        if name not in skipped
    )


def _no_cache_age(_field_name: str) -> float | None:
    """Default :data:`AgeLookup` when the caller has no cache layer."""
    return None
