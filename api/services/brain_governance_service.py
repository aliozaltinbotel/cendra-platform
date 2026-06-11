"""Cendra brain governance service layer (Batch 5).

Thin orchestration over the kernel for the service_api surface:
trust-meter views, owner-policy documents (parse -> compile -> Z3-able
storage), audit/case queries, and the T6 external-knowledge retrieval.
Tenant-scoped throughout; sessions come from the Dify engine.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.abstention.calibrator import DEFAULT_MIN_SAMPLES
from core.brain.abstention.protocols import DEFAULT_WINDOW_SIZE
from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.sa_store import SQLAlchemyAutonomyStore, SQLAlchemyWorkflowKindRegistry
from core.brain.autonomy.trust_meter import TrustMeterService
from core.brain.compliance import PIIDetector, redact
from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
from core.brain.patterns.models import DecisionCase, DecisionType
from core.brain.patterns.shadow_verdict import read_shadow_verdict, verdict_of
from core.brain.policy.compiler import OwnerPolicyCompiler
from core.brain.policy.parser import OwnerPolicyParser
from extensions.ext_database import db
from models.brain_autonomy import BrainWorkflowKind
from models.brain_calibration import BrainCalibrationSample
from models.brain_decision import BrainDecisionCase
from models.brain_policy import BrainOwnerPolicy

logger = logging.getLogger(__name__)

_METRICS_MAX_WINDOW_DAYS = 90
_UNKNOWN_WORKFLOW = "unknown"
_VERDICT_KEYS = ("would_act", "would_abstain", "unknown")


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


@dataclass(frozen=True, slots=True)
class _WorkflowRegistryCache:
    """Cached per-tenant workflow-kind aliases for one metrics read.

    The accrual endpoint buckets rows by operator-facing workflow kind,
    not by raw tool id. The registry rows are the canonical alias map:
    ``send_access_code`` -> ``code_release`` and so on. Unknown tool ids
    stay visible as-is so product surfaces can still account for traffic
    before the pack registry is updated.
    """

    alias_to_workflow: dict[str, str]
    labels: dict[str, str]

    def resolve(self, tool_id: str | None) -> str:
        if tool_id is None:
            return _UNKNOWN_WORKFLOW
        normalized = tool_id.strip()
        if not normalized:
            return _UNKNOWN_WORKFLOW
        return self.alias_to_workflow.get(normalized.lower(), normalized)

    def label_for(self, workflow: str) -> str:
        return self.labels.get(workflow, workflow)


def _empty_verdict_counts() -> dict[str, int]:
    return dict.fromkeys(_VERDICT_KEYS, 0)


def _record_verdict(counts: dict[str, int], verdict: str) -> None:
    counts[verdict if verdict in counts else "unknown"] += 1


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _day_window(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = _day_start(date_from)
    end = _day_start(date_to + timedelta(days=1))
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _iter_days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _utc_isoformat(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC).isoformat()
    return moment.astimezone(UTC).isoformat()


def _workflow_scope(
    workflow_registry: _WorkflowRegistryCache,
    workflow: str | None,
) -> str | None:
    normalized = _first_text(workflow)
    if normalized is None:
        return None
    return workflow_registry.resolve(normalized)


def _workflow_from_case_payload(
    decision: Any,
    outcome: Any,
    orchestrator_verdict: Any,
    workflow_registry: _WorkflowRegistryCache,
) -> str:
    decision_payload = _mapping(decision)
    decision_params = _mapping(decision_payload.get("params"))
    outcome_payload = _mapping(outcome)
    orchestrator_payload = _mapping(orchestrator_verdict)
    explicit_workflow = _first_text(
        decision_params.get("workflow"),
        decision_params.get("workflow_kind"),
        outcome_payload.get("workflow"),
        outcome_payload.get("workflow_kind"),
        orchestrator_payload.get("workflow"),
    )
    if explicit_workflow is not None:
        return workflow_registry.resolve(explicit_workflow)
    event_alias = _first_text(
        decision_params.get("tool_id"),
        decision_params.get("event_type"),
        decision_params.get("action_kind"),
        orchestrator_payload.get("tool_id"),
    )
    return workflow_registry.resolve(event_alias)


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

    # ── case metrics (bounded accrual aggregates, CEN-32) ────────── #

    def case_metrics(
        self,
        *,
        date_from: date,
        date_to: date,
        workflow: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate the dispatch ledger over one inclusive UTC date window.

        The capture-integrity counts honour the requested window on both
        sources: ``brain_decision_cases`` for captured rows and
        ``brain_calibration_samples`` for dispatched observations. The
        per-workflow calibration coverage, however, reflects the current
        bounded calibration window because that is what the abstention
        gate consults at runtime.  ``workflow`` optionally narrows the
        aggregates to one automation id (stable workflow kind or raw
        event alias) and surfaces recency on that scoped bucket.
        """
        span_days = (date_to - date_from).days + 1
        if span_days <= 0:
            raise ValueError("date_to must be on or after date_from")
        if span_days > _METRICS_MAX_WINDOW_DAYS:
            raise ValueError(f"date range must be {_METRICS_MAX_WINDOW_DAYS} days or fewer")

        window_start, window_end = _day_window(date_from, date_to)
        verdict_counts = _empty_verdict_counts()
        by_day = {
            day.isoformat(): {
                "date": day.isoformat(),
                "captured_count": 0,
                "dispatched_count": 0,
                "verdict_counts": _empty_verdict_counts(),
            }
            for day in _iter_days(date_from, date_to)
        }
        by_workflow: dict[str, dict[str, Any]] = {}
        current_sample_sizes: dict[str, int] = defaultdict(int)

        with self._sessions() as session:
            workflow_registry = self._workflow_registry(session)
            workflow_filter = _workflow_scope(workflow_registry, workflow)
            case_rows = session.execute(
                select(
                    BrainDecisionCase.created_at,
                    BrainDecisionCase.decision,
                    BrainDecisionCase.outcome,
                    BrainDecisionCase.orchestrator_verdict,
                ).where(
                    BrainDecisionCase.tenant_id == self._tenant_id,
                    BrainDecisionCase.decision_type == DecisionType.DISPATCH.value,
                    BrainDecisionCase.archived_at.is_(None),
                    BrainDecisionCase.created_at >= window_start,
                    BrainDecisionCase.created_at < window_end,
                )
            ).all()
            dispatched_rows = session.execute(
                select(
                    BrainCalibrationSample.recorded_at,
                    BrainCalibrationSample.tool_id,
                ).where(
                    BrainCalibrationSample.tenant_id == self._tenant_id,
                    BrainCalibrationSample.recorded_at >= window_start,
                    BrainCalibrationSample.recorded_at < window_end,
                )
            ).all()
            calibration_tool_ids = session.execute(
                select(BrainCalibrationSample.tool_id).where(
                    BrainCalibrationSample.tenant_id == self._tenant_id,
                )
            ).scalars()
            for tool_id in calibration_tool_ids:
                resolved_workflow = workflow_registry.resolve(tool_id)
                if workflow_filter is not None and resolved_workflow != workflow_filter:
                    continue
                current_sample_sizes[resolved_workflow] += 1

        def ensure_workflow_bucket(workflow_name: str) -> dict[str, Any]:
            return by_workflow.setdefault(
                workflow_name,
                {
                    "workflow": workflow_name,
                    "label": workflow_registry.label_for(workflow_name),
                    "captured_count": 0,
                    "dispatched_count": 0,
                    "verdict_counts": _empty_verdict_counts(),
                    "latest_case_at": None,
                    "latest_dispatch_at": None,
                },
            )

        for row in dispatched_rows:
            day_key = row.recorded_at.date().isoformat()
            resolved_workflow = workflow_registry.resolve(row.tool_id)
            if workflow_filter is not None and resolved_workflow != workflow_filter:
                continue
            by_day[day_key]["dispatched_count"] += 1
            bucket = ensure_workflow_bucket(resolved_workflow)
            bucket["dispatched_count"] += 1
            latest_dispatch_at = bucket["latest_dispatch_at"]
            if latest_dispatch_at is None or row.recorded_at > latest_dispatch_at:
                bucket["latest_dispatch_at"] = row.recorded_at

        for row in case_rows:
            day_key = row.created_at.date().isoformat()
            verdict = verdict_of(row.orchestrator_verdict)
            resolved_workflow = _workflow_from_case_payload(
                row.decision,
                row.outcome,
                row.orchestrator_verdict,
                workflow_registry,
            )
            if workflow_filter is not None and resolved_workflow != workflow_filter:
                continue
            by_day_bucket = by_day[day_key]
            by_day_bucket["captured_count"] += 1
            _record_verdict(by_day_bucket["verdict_counts"], verdict)
            _record_verdict(verdict_counts, verdict)
            workflow_bucket = ensure_workflow_bucket(resolved_workflow)
            workflow_bucket["captured_count"] += 1
            _record_verdict(workflow_bucket["verdict_counts"], verdict)
            latest_case_at = workflow_bucket["latest_case_at"]
            if latest_case_at is None or row.created_at > latest_case_at:
                workflow_bucket["latest_case_at"] = row.created_at

        if workflow_filter is not None:
            ensure_workflow_bucket(workflow_filter)

        workflow_rows = sorted(
            by_workflow.values(),
            key=lambda bucket: (
                -int(bucket["captured_count"]),
                -int(bucket["dispatched_count"]),
                str(bucket["workflow"]),
            ),
        )
        covered_workflow_count = 0
        for bucket in workflow_rows:
            sample_size = current_sample_sizes.get(str(bucket["workflow"]), 0)
            covered = sample_size >= DEFAULT_MIN_SAMPLES
            if covered:
                covered_workflow_count += 1
            bucket["calibration_window"] = {
                "sample_size": sample_size,
                "covered": covered,
            }
            bucket["latest_case_at"] = _utc_isoformat(bucket["latest_case_at"])
            bucket["latest_dispatch_at"] = _utc_isoformat(bucket["latest_dispatch_at"])

        captured_count = sum(int(bucket["captured_count"]) for bucket in workflow_rows)
        dispatched_count = sum(int(bucket["dispatched_count"]) for bucket in workflow_rows)
        active_workflow_count = len(workflow_rows)
        return {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "generated_at": datetime.now(UTC).isoformat(),
            "capture_integrity": {
                "captured_count": captured_count,
                "dispatched_count": dispatched_count,
                "capture_rate": (round(captured_count / dispatched_count, 4) if dispatched_count else None),
            },
            "calibration_window": {
                "window_size": DEFAULT_WINDOW_SIZE,
                "min_samples": DEFAULT_MIN_SAMPLES,
                "active_workflow_count": active_workflow_count,
                "covered_workflow_count": covered_workflow_count,
                "coverage_rate": (
                    round(covered_workflow_count / active_workflow_count, 4) if active_workflow_count else None
                ),
            },
            "by_day": [by_day[day.isoformat()] for day in _iter_days(date_from, date_to)],
            "by_workflow": workflow_rows,
            "by_verdict": verdict_counts,
        }

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

    def _workflow_registry(self, session) -> _WorkflowRegistryCache:
        rows = session.execute(
            select(BrainWorkflowKind).where(
                BrainWorkflowKind.tenant_id == self._tenant_id,
                BrainWorkflowKind.enabled.is_(True),
            )
        ).scalars()
        alias_to_workflow: dict[str, str] = {}
        labels: dict[str, str] = {}
        for row in rows:
            alias_to_workflow[row.kind.lower()] = row.kind
            labels[row.kind] = row.label or row.kind
            for alias in row.event_aliases or ():
                alias_to_workflow[str(alias).lower()] = row.kind
        return _WorkflowRegistryCache(alias_to_workflow=alias_to_workflow, labels=labels)
