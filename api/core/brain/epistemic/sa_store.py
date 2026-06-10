"""SQLAlchemy-backed observation / belief stores (tenant-scoped).

Persistent implementations of the :class:`ObservationStore` and
:class:`BeliefStore` Protocols in :mod:`core.brain.epistemic.store`.
The reference (@a761e29) never shipped a persistent epistemic store —
written fresh per porting rule 7, keeping the Protocol contracts:

- observations are append-only; ``observations_for`` returns insertion
  order (oldest → newest), backed here by the time-ordered uuidv7
  surrogate key.
- ``promote`` overwrites the subject's current belief (one row per
  (tenant, subject)); history stays on the observation log.

The observation ``value`` is wrapped as ``{"value": <payload>}`` in the
JSON column so scalars / bools / ``None`` round-trip unambiguously, and
the kernel's BLAKE2B ``integrity_hex`` is persisted verbatim so
at-rest tampering remains detectable via
:func:`core.brain.epistemic.models.observation_integrity_hash`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.epistemic.models import (
    Belief,
    Observation,
    Provenance,
    ProvenanceKind,
)
from models.brain_epistemic import BrainBelief, BrainObservation

__all__ = [
    "SQLAlchemyBeliefStore",
    "SQLAlchemyObservationStore",
]


logger = logging.getLogger(__name__)


def _to_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _to_aware(moment: datetime) -> datetime:
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


def _row_to_observation(row: BrainObservation) -> Observation:
    return Observation(
        observation_id=row.observation_id,
        subject=row.subject,
        value=(row.value or {}).get("value"),
        recorded_at=_to_aware(row.recorded_at),
        provenance=Provenance(
            kind=ProvenanceKind(row.provenance_kind),
            source_id=row.provenance_source_id,
            correlation_id=row.provenance_correlation_id,
        ),
        integrity_hex=row.integrity_hex,
    )


def _row_to_belief(row: BrainBelief) -> Belief:
    return Belief(
        belief_id=row.belief_id,
        subject=row.subject,
        promoted_value=(row.promoted_value or {}).get("value"),
        wilson_lb=float(row.wilson_lb),
        sample_size=int(row.sample_size),
        supporting_observation_ids=tuple(row.supporting_observation_ids or ()),
        promoted_at=_to_aware(row.promoted_at),
        promoted_by=row.promoted_by,
        extra=dict(row.extra or {}),
    )


class SQLAlchemyObservationStore:
    """Tenant-scoped append-only :class:`ObservationStore`."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def record(self, observation: Observation) -> None:
        """Append an observation; re-recording an existing id is a no-op."""
        with self._session_maker() as session:
            exists = session.execute(
                select(BrainObservation.id).where(
                    BrainObservation.tenant_id == self._tenant_id,
                    BrainObservation.observation_id == observation.observation_id,
                )
            ).first()
            if exists is not None:
                return
            session.add(
                BrainObservation(
                    tenant_id=self._tenant_id,
                    observation_id=observation.observation_id,
                    subject=observation.subject,
                    value={"value": observation.value},
                    recorded_at=_to_naive(observation.recorded_at),
                    provenance_kind=observation.provenance.kind.value,
                    provenance_source_id=observation.provenance.source_id,
                    provenance_correlation_id=observation.provenance.correlation_id,
                    integrity_hex=observation.integrity_hex,
                )
            )
            session.commit()
        logger.debug("observation_recorded subject=%s", observation.subject)

    def observations_for(self, subject: str) -> Sequence[Observation]:
        """Return the subject's window in insertion order (oldest first)."""
        with self._session_maker() as session:
            rows = (
                session.execute(
                    select(BrainObservation)
                    .where(
                        BrainObservation.tenant_id == self._tenant_id,
                        BrainObservation.subject == subject,
                    )
                    .order_by(BrainObservation.id.asc())
                )
                .scalars()
                .all()
            )
            return tuple(_row_to_observation(row) for row in rows)

    def known_subjects(self) -> tuple[str, ...]:
        """Return the subjects with at least one recorded observation."""
        with self._session_maker() as session:
            rows = session.execute(
                select(BrainObservation.subject).where(BrainObservation.tenant_id == self._tenant_id).distinct()
            ).all()
            return tuple(subject for (subject,) in rows)


class SQLAlchemyBeliefStore:
    """Tenant-scoped current-state :class:`BeliefStore`."""

    def __init__(self, *, session_maker: sessionmaker, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._session_maker = session_maker
        self._tenant_id = tenant_id

    def promote(self, belief: Belief) -> None:
        """Persist ``belief`` as the subject's current snapshot (overwrite)."""
        with self._session_maker() as session:
            row = session.execute(
                select(BrainBelief).where(
                    BrainBelief.tenant_id == self._tenant_id,
                    BrainBelief.subject == belief.subject,
                )
            ).scalar_one_or_none()
            if row is None:
                row = BrainBelief(
                    tenant_id=self._tenant_id,
                    belief_id=belief.belief_id,
                    subject=belief.subject,
                    promoted_at=_to_naive(belief.promoted_at),
                    promoted_by=belief.promoted_by,
                )
                session.add(row)
            row.belief_id = belief.belief_id
            row.promoted_value = {"value": belief.promoted_value}
            row.wilson_lb = belief.wilson_lb
            row.sample_size = belief.sample_size
            row.supporting_observation_ids = list(belief.supporting_observation_ids)
            row.promoted_at = _to_naive(belief.promoted_at)
            row.promoted_by = belief.promoted_by
            row.extra = dict(belief.extra)
            session.commit()
        logger.debug("belief_promoted subject=%s wilson_lb=%s", belief.subject, belief.wilson_lb)

    def current(self, subject: str) -> Belief | None:
        with self._session_maker() as session:
            row = session.execute(
                select(BrainBelief).where(
                    BrainBelief.tenant_id == self._tenant_id,
                    BrainBelief.subject == subject,
                )
            ).scalar_one_or_none()
            return None if row is None else _row_to_belief(row)

    def known_subjects(self) -> tuple[str, ...]:
        """Return the subjects with a current belief."""
        with self._session_maker() as session:
            rows = session.execute(select(BrainBelief.subject).where(BrainBelief.tenant_id == self._tenant_id)).all()
            return tuple(subject for (subject,) in rows)
