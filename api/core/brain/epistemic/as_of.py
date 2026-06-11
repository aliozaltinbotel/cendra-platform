"""As-of valid-time semantics for External-Knowledge retrieval (Moat #5).

CEN-15 Part A (Path 2): the kernel's External-Knowledge endpoint (T6
loopback, ``POST /v1/brain/retrieval``) accepts a decision-time
``as_of`` and must answer "what did the corpus hold *as believed at*
``as_of``" — not "what does it say now".  This module is the pure,
store-free half of that contract so the rules are unit-testable without
Qdrant / Postgres:

- :func:`parse_as_of` — strict RFC3339/ISO-8601 parsing of the request
  field (unparseable → ``ValueError`` → the controller's ``400``).
- :func:`visible_as_of` — the bi-temporal visibility rule: the
  valid-time window must contain ``as_of`` **and** the fact must have
  been recorded by ``as_of`` (no facts asserted after ``T``).
- :func:`document_validity` — ingest-side normalisation of Dify
  ``doc_metadata`` ``valid_from``/``valid_to`` into a
  :class:`DocumentValidity`, applying the adjudicated CEN-15 ruling:
  ``valid_from`` is required only for newly indexed docs; migrated
  corpora default to the upload date and carry an
  ``valid_window_unverified`` flag.
- :func:`validity_observation` — wraps a :class:`DocumentValidity`
  into an immutable epistemic :class:`Observation` (subject
  ``doc:<document_id>:validity``) so index-time ingest lands in the
  same store the as-of reconstruction reads.
- :func:`bitemporal_provenance` — the per-record provenance block the
  retrieve response carries (``valid_from``/``valid_to``/
  ``recorded_at``/``as_of_used``/``retrieved_at``/``kg_snapshot_ref``).
- :func:`kg_snapshot_ref` — deterministic pointer
  ``brain:kg:<subject>@<as_of-iso>`` linking a result (or a gap record,
  see :mod:`core.brain.abstention.gap_registry`) to the belief snapshot
  used.

Decision-time semantics are the adjudicated §E1 ruling: ``as_of`` is
the run's **inbound-event timestamp** (when the triggering event
arrived), never wall-clock at retrieval; the dispatch wall-clock
travels separately as ``retrieved_at`` so provenance carries both
timelines.  Transaction-time reconstruction over the ``brain:kg:``
keyspace stays in :mod:`core.brain.memory.kg_as_of`; this module rules
on the chunk-metadata projection served through T6.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.brain.epistemic.models import (
    Observation,
    Provenance,
    ProvenanceKind,
    observation_integrity_hash,
)

__all__ = [
    "VALIDITY_SUBJECT_PREFIX",
    "DocumentValidity",
    "bitemporal_provenance",
    "document_validity",
    "kg_snapshot_ref",
    "parse_as_of",
    "validity_observation",
    "visible_as_of",
]


VALIDITY_SUBJECT_PREFIX = "doc:"


def parse_as_of(raw: str) -> datetime:
    """Parse the request's ``as_of`` field to an aware UTC datetime.

    Accepts RFC3339 / ISO-8601 (a trailing ``Z`` is normalised); naive
    timestamps are treated as UTC.  Raises :class:`ValueError` on
    anything unparseable — the controller maps that to ``400``.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("as_of must be a non-empty RFC3339 timestamp")
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unparseable as_of: {raw!r}") from exc
    return _as_utc(parsed)


def visible_as_of(
    *,
    as_of: datetime,
    valid_from: datetime | None,
    valid_to: datetime | None,
    recorded_at: datetime | None,
) -> bool:
    """Return whether a fact is part of the belief as of ``as_of``.

    The contract guarantee for every returned chunk:
    ``valid_from <= as_of < valid_to`` (``valid_to`` ``None`` = still
    valid), and the fact must have been *known* by then
    (``recorded_at <= as_of``).  Missing ``valid_from`` /
    ``recorded_at`` degrade to visible rather than hidden — pre-ingest
    corpora must not vanish from retrieval; they are flagged
    ``valid_window_unverified`` in provenance instead.
    """
    as_of = _as_utc(as_of)
    if recorded_at is not None and _as_utc(recorded_at) > as_of:
        return False
    if valid_from is not None and _as_utc(valid_from) > as_of:
        return False
    if valid_to is not None and _as_utc(valid_to) <= as_of:
        return False
    return True


@dataclass(frozen=True, slots=True)
class DocumentValidity:
    """Normalised valid-time window for one indexed document.

    ``unverified_window`` marks the adjudicated migrated-corpus
    default: no operator-asserted ``valid_from`` existed, so the upload
    date was substituted and the window must not be presented as an
    operator assertion.
    """

    document_id: str
    valid_from: datetime
    valid_to: datetime | None
    unverified_window: bool

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("document_id required")
        if self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be tz-aware")
        if self.valid_to is not None and self.valid_to.tzinfo is None:
            raise ValueError("valid_to must be tz-aware")


def document_validity(
    *,
    document_id: str,
    doc_metadata: Mapping[str, Any] | None,
    uploaded_at: datetime,
) -> DocumentValidity:
    """Build a :class:`DocumentValidity` from Dify ``doc_metadata``.

    Applies the CEN-15 adjudication: ``valid_from`` is honoured when
    the operator asserted it; otherwise the document defaults to its
    upload date with ``unverified_window=True``.  ``valid_to`` is
    optional throughout (``None`` = still in force).
    """
    if uploaded_at.tzinfo is None:
        raise ValueError("uploaded_at must be tz-aware")
    metadata = doc_metadata or {}
    raw_from = metadata.get("valid_from")
    raw_to = metadata.get("valid_to")
    valid_from = _parse_optional(raw_from)
    valid_to = _parse_optional(raw_to)
    if valid_from is None:
        return DocumentValidity(
            document_id=document_id,
            valid_from=uploaded_at.astimezone(UTC),
            valid_to=valid_to,
            unverified_window=True,
        )
    return DocumentValidity(
        document_id=document_id,
        valid_from=valid_from,
        valid_to=valid_to,
        unverified_window=False,
    )


def validity_observation(
    validity: DocumentValidity,
    *,
    recorded_at: datetime,
    source_id: str,
    observation_id: str | None = None,
) -> Observation:
    """Wrap a document's valid-time window into an epistemic observation.

    Index-time ingest records the window as immutable evidence under
    subject ``doc:<document_id>:validity`` — transaction-time
    (``recorded_at``) is when the kernel learned the window, which is
    exactly what the as-of reconstruction needs to exclude facts
    asserted after ``T``.  Re-ingesting after an operator edit appends
    a fresh observation; the log keeps the history.
    """
    oid = observation_id or str(uuid4())
    subject = f"{VALIDITY_SUBJECT_PREFIX}{validity.document_id}:validity"
    value = {
        "valid_from": validity.valid_from.isoformat(),
        "valid_to": validity.valid_to.isoformat() if validity.valid_to else None,
        "valid_window_unverified": validity.unverified_window,
    }
    provenance = Provenance(kind=ProvenanceKind.SYSTEM, source_id=source_id)
    return Observation(
        observation_id=oid,
        subject=subject,
        value=value,
        recorded_at=recorded_at,
        provenance=provenance,
        integrity_hex=observation_integrity_hash(
            observation_id=oid,
            subject=subject,
            value=value,
            recorded_at=recorded_at,
            provenance=provenance,
        ),
    )


def kg_snapshot_ref(subject: str, as_of: datetime) -> str:
    """Deterministic pointer to the belief snapshot used at ``as_of``.

    Convention: ``brain:kg:<subject>@<as_of UTC ISO-8601>``.  Stable and
    reconstructible — both the retrieve provenance and the knowledge-gap
    registry stamp this so a gap can be replayed against exactly what
    the corpus held when the system abstained.
    """
    if not subject:
        raise ValueError("subject required")
    return f"brain:kg:{subject}@{_as_utc(as_of).isoformat()}"


def bitemporal_provenance(
    record_metadata: Mapping[str, Any] | None,
    *,
    as_of_used: datetime | None,
    retrieved_at: datetime,
    snapshot_ref: str,
) -> dict[str, Any]:
    """Build the provenance block one retrieve record carries.

    Merges the record's own metadata (preserved verbatim) with the
    bi-temporal contract fields.  ``retrieved_at`` (dispatch
    wall-clock) is always present; ``as_of_used`` echoes the effective
    decision-time and is ``None`` when the caller asked for current
    belief.  Records whose window was never operator-asserted keep
    their ``valid_window_unverified`` flag.
    """
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be tz-aware")
    metadata = dict(record_metadata or {})
    valid_from = _parse_optional(metadata.get("valid_from"))
    valid_to = _parse_optional(metadata.get("valid_to"))
    recorded_at = _parse_optional(metadata.get("recorded_at"))
    metadata.update(
        {
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "recorded_at": recorded_at.isoformat() if recorded_at else None,
            "as_of_used": _as_utc(as_of_used).isoformat() if as_of_used else None,
            "retrieved_at": _as_utc(retrieved_at).isoformat(),
            "kg_snapshot_ref": snapshot_ref,
        }
    )
    if valid_from is None:
        metadata["valid_window_unverified"] = True
    return metadata


def _parse_optional(value: Any) -> datetime | None:
    """Lenient optional-timestamp parse used over stored metadata.

    Stored metadata is not a request boundary: a malformed stamp must
    degrade to "unknown" rather than fail retrieval.
    """
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_as_of(value)
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
