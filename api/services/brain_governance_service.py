"""Cendra brain governance service layer (Batch 5).

Thin orchestration over the kernel for the service_api surface:
trust-meter views, owner-policy documents (parse -> compile -> Z3-able
storage), audit/case queries, and the T6 external-knowledge retrieval.
Tenant-scoped throughout; sessions come from the Dify engine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.sa_store import SQLAlchemyAutonomyStore, SQLAlchemyWorkflowKindRegistry
from core.brain.autonomy.trust_meter import TrustMeterService
from core.brain.compliance import PIIDetector, redact
from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
from core.brain.patterns.models import DecisionCase
from core.brain.patterns.shadow_verdict import read_shadow_verdict, verdict_of
from core.brain.policy.compiler import OwnerPolicyCompiler
from core.brain.policy.parser import OwnerPolicyParser
from extensions.ext_database import db
from models.brain_policy import BrainOwnerPolicy

logger = logging.getLogger(__name__)


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


def _parse_meta_dt(value: Any) -> datetime | None:
    """Lenient ISO parse over stored provenance values (None on junk)."""
    if not isinstance(value, str) or not value:
        return None
    from core.brain.epistemic.as_of import parse_as_of

    try:
        return parse_as_of(value)
    except ValueError:
        return None


def _pii_safe(text: str, detector: PIIDetector) -> str:
    """Redact PII spans before message context leaves the kernel (T4).

    The Decision Card surfaces guest/PM message text; it must never
    carry raw identifiers (email, phone, national ID, IBAN …) into the
    dashboard. Empty text short-circuits so we never pay a scan on the
    common no-text case.
    """
    if not text:
        return ""
    return redact(text, detector.scan(text))


def _serialize_case(case: DecisionCase, detector: PIIDetector) -> dict[str, Any]:
    """Decision Card read model for ``GET /v1/brain/cases`` (CEN-45).

    Exposes the kernel :class:`CaseOutcome` governance fields, the
    PII-safe message context, and the observe-posture shadow verdict
    (CEN-33) the Decision Card and dashboard summary tiles need
    (CEN-19 PRD §4.1). Exposure only — every value is already carried on
    the in-memory case (``CaseOutcome`` + ``orchestrator_verdict`` JSONB).

    ``verdict`` is the act/abstain KPI bucket (``would_act`` /
    ``would_abstain`` / ``unknown`` for pre-capture rows); ``confidence``
    is the gate-chain confidence the shadow block recorded, or ``None``
    when no shadow verdict was captured.
    """
    outcome = case.outcome
    shadow = read_shadow_verdict(case.orchestrator_verdict)
    return {
        "case_id": case.case_id,
        "stage": case.stage,
        "scenario": case.scenario,
        "property_id": case.property_id,
        "decision": case.decision.action_type.value,
        "successful": outcome.successful,
        "human_overrode": outcome.human_overrode,
        "resolution_type": outcome.resolution_type.value if outcome.resolution_type else None,
        "revenue_impact": outcome.revenue_impact,
        "approval_required": outcome.approval_required,
        "approved": outcome.approved,
        "conversation_id": case.reservation_id,
        "message_text": _pii_safe(case.message_text, detector),
        "response_text": _pii_safe(case.response_text, detector),
        "verdict": verdict_of(case.orchestrator_verdict),
        "confidence": shadow.get("confidence") if shadow else None,
        "created_at": case.created_at.isoformat(),
        "decision_at": case.decision_at.isoformat() if case.decision_at else None,
    }


class BrainGovernanceService:
    """Tenant-scoped facade consumed by the brain controllers."""

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id
        self._sessions = _session_maker()

    # ── trust meter ────────────────────────────────────────────── #

    def trust_meter(self, property_id: str) -> dict[str, Any]:
        registry = SQLAlchemyWorkflowKindRegistry(session_maker=self._sessions, tenant_id=self._tenant_id)
        engine = AutonomyEngine(store=SQLAlchemyAutonomyStore(session_maker=self._sessions, tenant_id=self._tenant_id))
        labels = registry.labels()  # kind -> operator-facing label (defaults to kind)
        view = TrustMeterService(engine=engine, workflows=registry.kinds()).for_property(property_id)
        return {
            "property_id": view.property_id,
            "generated_at": view.generated_at.isoformat(),
            "bands": [
                {
                    "workflow": band.workflow,  # stable wire kind ID (rename = breaking)
                    "label": labels.get(band.workflow) or band.workflow,  # never null
                    "state": band.state.value,
                    "sample_size": band.sample_size,
                    "success_rate": band.success_rate,
                    "override_rate": band.override_rate,
                    "incidents": band.incidents,
                    "progress": {
                        "target_state": band.progress.target_state.value if band.progress.target_state else None,
                        "satisfied": band.progress.satisfied_count,
                        "total": band.progress.total,
                    },
                }
                for band in view.bands
            ],
            "labels": labels,  # full kind->label vocabulary for all enabled kinds
        }

    # ── owner policy ───────────────────────────────────────────── #

    def save_policy(self, owner_id: str, document_text: str) -> dict[str, Any]:
        """Parse + compile (validates the DSL) and upsert the document."""
        document = OwnerPolicyParser().parse(document_text)
        compiled = OwnerPolicyCompiler().compile(document)
        with self._sessions() as session:
            row = session.execute(
                select(BrainOwnerPolicy).where(
                    BrainOwnerPolicy.tenant_id == self._tenant_id,
                    BrainOwnerPolicy.owner_id == owner_id,
                )
            ).scalar_one_or_none()
            if row is None:
                row = BrainOwnerPolicy(tenant_id=self._tenant_id, owner_id=owner_id, document_text=document_text)
                session.add(row)
            row.document_text = document_text
            row.compiled = {
                "styles": sorted(compiled.styles),
                "owner_style": dict(compiled.owner_style),
                "jurisdictions": dict(compiled.jurisdictions),
            }
            row.active = True
            session.commit()
        return {"owner_id": owner_id, "active": True}

    def get_policy(self, owner_id: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.execute(
                select(BrainOwnerPolicy).where(
                    BrainOwnerPolicy.tenant_id == self._tenant_id,
                    BrainOwnerPolicy.owner_id == owner_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "owner_id": row.owner_id,
                "document_text": row.document_text,
                "active": row.active,
                "updated_at": row.updated_at.isoformat(),
            }

    # ── audit / cases ──────────────────────────────────────────── #

    def list_cases(self, *, property_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        store = SQLAlchemyDecisionCaseStore(session_maker=self._sessions, tenant_id=self._tenant_id)
        cases = store.search(property_id=property_id, limit=limit, offset=offset)
        detector = PIIDetector()
        return [_serialize_case(case, detector) for case in cases]

    # ── T6 retrieval (external knowledge loopback) ─────────────── #

    def retrieve_memory(
        self,
        query: str,
        *,
        top_k: int = 5,
        score_threshold: float = 0.0,
        as_of: datetime | None = None,
    ) -> list[dict]:
        """Serve brain semantic memory to Dify knowledge nodes (T6).

        With ``as_of`` (the run's inbound-event timestamp, CEN-15
        ruling §E1) only chunks visible at that decision-time are
        returned — valid-time window contains ``as_of`` and the fact
        was recorded by then.  Every record carries the bi-temporal
        provenance block (``as_of_used`` / ``retrieved_at`` /
        ``kg_snapshot_ref`` …); ``as_of`` omitted serves current belief
        (standard behavior, ``as_of_used`` null).

        Degrades to an empty result when the embedding pod / Qdrant
        are unavailable — a knowledge node must never hard-fail on the
        brain tier.
        """
        try:
            from core.brain.epistemic.as_of import bitemporal_provenance, kg_snapshot_ref, visible_as_of
            from core.brain.memory.semantic_memory import SemanticMemory

            memory = SemanticMemory(collection_name=f"brain_semantic_{self._tenant_id}")
            records = memory.search(query=query, top_k=top_k, score_threshold=score_threshold)
            retrieved_at = datetime.now(UTC)
            snapshot_ref = kg_snapshot_ref(f"tenant:{self._tenant_id}", as_of or retrieved_at)
            results: list[dict] = []
            for r in records:
                provenance = bitemporal_provenance(
                    r.metadata,
                    as_of_used=as_of,
                    retrieved_at=retrieved_at,
                    snapshot_ref=snapshot_ref,
                )
                if as_of is not None and not visible_as_of(
                    as_of=as_of,
                    valid_from=_parse_meta_dt(provenance.get("valid_from")),
                    valid_to=_parse_meta_dt(provenance.get("valid_to")),
                    recorded_at=_parse_meta_dt(provenance.get("recorded_at")),
                ):
                    continue
                results.append(
                    {
                        "content": r.text,
                        "score": r.score,
                        "title": r.metadata.get("title", ""),
                        "metadata": provenance,
                    }
                )
            return results
        except Exception:
            logger.exception("brain retrieval degraded to empty result")
            return []

    def ingest_document_validity(
        self,
        *,
        document_id: str,
        doc_metadata: dict[str, Any] | None,
        uploaded_at: datetime,
    ) -> dict[str, Any]:
        """Ingest a document's valid-time window into the epistemic store.

        Index-time hook (CEN-15 Part A): normalises Dify ``doc_metadata``
        ``valid_from``/``valid_to`` per the adjudicated ruling (missing
        ``valid_from`` defaults to the upload date with an
        unverified-window flag) and records it as an append-only
        observation under ``doc:<document_id>:validity``.
        """
        from core.brain.epistemic.as_of import document_validity, validity_observation
        from core.brain.epistemic.sa_store import SQLAlchemyObservationStore

        validity = document_validity(
            document_id=document_id,
            doc_metadata=doc_metadata,
            uploaded_at=uploaded_at,
        )
        observation = validity_observation(
            validity,
            recorded_at=datetime.now(UTC),
            source_id=f"dify:doc_metadata:{document_id}",
        )
        SQLAlchemyObservationStore(session_maker=self._sessions, tenant_id=self._tenant_id).record(observation)
        return {
            "document_id": validity.document_id,
            "valid_from": validity.valid_from.isoformat(),
            "valid_to": validity.valid_to.isoformat() if validity.valid_to else None,
            "valid_window_unverified": validity.unverified_window,
            "observation_id": observation.observation_id,
        }
