"""Cendra brain governance service layer (Batch 5).

Thin orchestration over the kernel for the service_api surface:
trust-meter views, owner-policy documents (parse -> compile -> Z3-able
storage), audit/case queries, and the T6 external-knowledge retrieval.
Tenant-scoped throughout; sessions come from the Dify engine.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from core.brain.autonomy.engine import AutonomyEngine
from core.brain.autonomy.sa_store import SQLAlchemyAutonomyStore, SQLAlchemyWorkflowKindRegistry
from core.brain.autonomy.trust_meter import TrustMeterService
from core.brain.patterns.case_store import SQLAlchemyDecisionCaseStore
from core.brain.policy.compiler import OwnerPolicyCompiler
from core.brain.policy.parser import OwnerPolicyParser
from extensions.ext_database import db
from models.brain_policy import BrainOwnerPolicy

logger = logging.getLogger(__name__)


def _session_maker() -> sessionmaker:
    return sessionmaker(bind=db.engine, expire_on_commit=False)


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
        return [
            {
                "case_id": case.case_id,
                "stage": case.stage,
                "scenario": case.scenario,
                "property_id": case.property_id,
                "decision": case.decision.action_type.value,
                "successful": case.outcome.successful,
                "conversation_id": case.reservation_id,
                "created_at": case.created_at.isoformat(),
            }
            for case in cases
        ]

    # ── T6 retrieval (external knowledge loopback) ─────────────── #

    def retrieve_memory(self, query: str, *, top_k: int = 5, score_threshold: float = 0.0) -> list[dict]:
        """Serve brain semantic memory to Dify knowledge nodes (T6).

        Degrades to an empty result when the embedding pod / Qdrant
        are unavailable — a knowledge node must never hard-fail on the
        brain tier.
        """
        try:
            from core.brain.memory.semantic_memory import SemanticMemory

            memory = SemanticMemory(collection_name=f"brain_semantic_{self._tenant_id}")
            records = memory.search(query=query, top_k=top_k, score_threshold=score_threshold)
            return [
                {"content": r.text, "score": r.score, "title": r.metadata.get("title", ""), "metadata": r.metadata}
                for r in records
            ]
        except Exception:
            logger.exception("brain retrieval degraded to empty result")
            return []
