"""Property Knowledge bi-temporal wiring — Dify side of CEN-15 (CEN-27).

Implements the Flow rows of the CEN-15 change ledger
(``docs/product/cen-15-bitemporal-anchoring-design.md`` §A.5):

1. **Valid-time metadata convention** (:func:`ensure_bitemporal_metadata`,
   :func:`stamp_document_validity`) — ``valid_from`` / ``valid_to`` as
   TIME-typed dataset metadata on internal (Dify-managed) Property Knowledge
   datasets, persisted in ``doc_metadata`` epoch seconds like the built-in
   ``upload_date`` field.  The kernel epistemic store remains source-of-truth
   for as-of reconstruction; this is the denormalized projection for Path-1
   filtering and operator-visible editing (design §A.4).
2. **Kernel External-Knowledge binding** (:func:`bind_dataset_to_kernel`) —
   create an external dataset bound to the brain kernel's External-Knowledge
   endpoint (Path 2), reusing upstream ``ExternalDatasetService`` rows and
   validation unchanged.
3. **Path-1 manual-mode valid-time filter**
   (:func:`validity_window_conditions`) — the table-stakes fallback.

**Path-1 labeling rule (binding, ruling §E3 + design §D):** Path-1 valid-time
filtering may be described only as table-stakes "always-current docs" — never
as a moat or differentiator.  No differentiation claim for Property Knowledge
until the Path-2 as-of loopback is live end-to-end and the moat-fit-map row
reads ``implemented``.

Decision-time threading for Path 2 lives in
:mod:`services.brain_decision_clock` (read at the marked T6 block in
``services/external_knowledge_service.py``); this module carries the
dataset-shape conventions only.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from extensions.ext_database import db
from models.dataset import Dataset, DatasetMetadata, DatasetMetadataBinding, Document, ExternalKnowledgeApis
from models.enums import DatasetMetadataType
from services.brain_decision_clock import kernel_knowledge_endpoint
from services.external_knowledge_service import ExternalDatasetService

VALID_FROM_FIELD = "valid_from"
VALID_TO_FIELD = "valid_to"

# Upstream metadata filtering is a flat condition list under ONE logical
# operator (core/rag/entities/metadata_entities.py), so the design's Path-1
# predicate ``valid_from <= T AND (valid_to is empty OR valid_to >= T)``
# (§A.3) is not expressible as written.  Convention instead: an open-ended
# window (``valid_to=None`` at stamping) stores this far-future sentinel, and
# the manual filter stays a plain conjunction.
OPEN_ENDED_VALID_TO = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp()


def _epoch(moment: datetime | float | int) -> float:
    """Coerce a valid-time bound to doc_metadata epoch seconds."""
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            raise ValueError("valid-time bounds must be timezone-aware datetimes")
        return moment.timestamp()
    return float(moment)


def ensure_bitemporal_metadata(tenant_id: str, dataset_id: str, user_id: str) -> dict[str, DatasetMetadata]:
    """Idempotently ensure the two TIME metadata fields exist on a dataset.

    Mirrors ``MetadataService.create_metadata`` row shape but takes explicit
    tenant/user ids so it is callable outside a console request context
    (Celery ingest, pack provisioning).  Raises if a same-named field already
    exists with a non-TIME type — that dataset has a conflicting convention.
    """
    ensured: dict[str, DatasetMetadata] = {}
    for name in (VALID_FROM_FIELD, VALID_TO_FIELD):
        existing = db.session.scalar(
            select(DatasetMetadata)
            .where(
                DatasetMetadata.tenant_id == tenant_id,
                DatasetMetadata.dataset_id == dataset_id,
                DatasetMetadata.name == name,
            )
            .limit(1)
        )
        if existing is not None:
            if existing.type != DatasetMetadataType.TIME:
                raise ValueError(f"metadata field {name!r} exists with type {existing.type!r}, expected 'time'")
            ensured[name] = existing
            continue
        row = DatasetMetadata(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            type=DatasetMetadataType.TIME,
            name=name,
            created_by=user_id,
        )
        db.session.add(row)
        ensured[name] = row
    db.session.commit()
    return ensured


def stamp_document_validity(
    dataset: Dataset,
    document_id: str,
    user_id: str,
    valid_from: datetime | float | int,
    valid_to: datetime | float | int | None = None,
) -> dict[str, float]:
    """Stamp a document's valid-time window into ``doc_metadata`` (partial update).

    ``valid_to=None`` means "still in force" and stores
    :data:`OPEN_ENDED_VALID_TO` (see the sentinel rationale above).  Existing
    unrelated metadata keys are preserved.  Returns the stored window.
    """
    window = {
        VALID_FROM_FIELD: _epoch(valid_from),
        VALID_TO_FIELD: _epoch(valid_to) if valid_to is not None else OPEN_ENDED_VALID_TO,
    }
    if window[VALID_FROM_FIELD] > window[VALID_TO_FIELD]:
        raise ValueError("valid_from must not be after valid_to")

    fields = ensure_bitemporal_metadata(dataset.tenant_id, dataset.id, user_id)
    document = db.session.scalar(
        select(Document).where(Document.id == document_id, Document.dataset_id == dataset.id).limit(1)
    )
    if document is None:
        raise ValueError("Document not found.")

    doc_metadata = copy.deepcopy(document.doc_metadata) if document.doc_metadata else {}
    doc_metadata.update(window)
    document.doc_metadata = doc_metadata
    db.session.add(document)

    for name, field in fields.items():
        existing_binding = db.session.scalar(
            select(DatasetMetadataBinding)
            .where(
                DatasetMetadataBinding.document_id == document_id,
                DatasetMetadataBinding.metadata_id == field.id,
            )
            .limit(1)
        )
        if existing_binding is None:
            db.session.add(
                DatasetMetadataBinding(
                    tenant_id=dataset.tenant_id,
                    dataset_id=dataset.id,
                    document_id=document_id,
                    metadata_id=field.id,
                    created_by=user_id,
                )
            )
    db.session.commit()
    return window


def validity_window_conditions(as_of: datetime | float | int | str | None = None) -> dict[str, Any]:
    """Path-1 manual-mode metadata filtering conditions (table-stakes fallback).

    Returns the ``metadata_filtering_conditions`` payload for a
    knowledge-retrieval node configured with
    ``metadata_filtering_mode="manual"``: ``valid_from before X AND valid_to
    after X``.  With the open-ended sentinel convention this selects exactly
    the documents whose valid-time window contains ``X``.

    ``as_of`` accepts an epoch number, a tz-aware datetime, or a workflow
    variable template string (e.g. ``"{{#start.event_ts#}}"``) which the node
    resolves per run; ``None`` uses wall-clock now ("always-current docs").
    The payload validates against ``core.rag.entities.metadata_entities
    .MetadataFilteringCondition`` — upstream's ``before``/``after`` are strict
    comparisons, an acceptable tightening of the design's ``<=``/``>=`` at
    one-second metadata granularity.
    """
    value: str | float
    if as_of is None:
        value = datetime.now(tz=UTC).timestamp()
    elif isinstance(as_of, str):
        value = as_of
    else:
        value = _epoch(as_of)
    return {
        "logical_operator": "and",
        "conditions": [
            {"name": VALID_FROM_FIELD, "comparison_operator": "before", "value": value},
            {"name": VALID_TO_FIELD, "comparison_operator": "after", "value": value},
        ],
    }


def bind_dataset_to_kernel(
    tenant_id: str,
    user_id: str,
    *,
    name: str,
    external_knowledge_id: str,
    api_key: str,
    description: str = "",
    top_k: int = 5,
    score_threshold: float = 0.5,
) -> Dataset:
    """Bind a Property Knowledge dataset to the kernel External-Knowledge endpoint (Path 2).

    Reuses the tenant's existing kernel ``ExternalKnowledgeApis`` row when one
    already points at the configured endpoint; otherwise creates it through
    upstream ``ExternalDatasetService`` (including its reachability check).
    ``external_knowledge_id`` is the kernel-side knowledge binding id the
    retrieve contract echoes back as ``knowledge_id``.
    """
    endpoint = kernel_knowledge_endpoint()
    if endpoint is None:
        raise ValueError("BRAIN_KERNEL_KNOWLEDGE_ENDPOINT is not configured")

    kernel_api = _find_kernel_knowledge_api(tenant_id, endpoint)
    if kernel_api is None:
        kernel_api = ExternalDatasetService.create_external_knowledge_api(
            tenant_id,
            user_id,
            {
                "name": "Cendra Brain Kernel",
                "description": "Bi-temporal Property Knowledge served by the brain kernel (CEN-15).",
                "settings": {"endpoint": endpoint, "api_key": api_key},
            },
        )

    return ExternalDatasetService.create_external_dataset(
        tenant_id,
        user_id,
        {
            "name": name,
            "description": description,
            "external_knowledge_api_id": kernel_api.id,
            "external_knowledge_id": external_knowledge_id,
            "external_retrieval_model": {
                "top_k": top_k,
                "score_threshold": score_threshold,
                "score_threshold_enabled": True,
            },
        },
    )


def _find_kernel_knowledge_api(tenant_id: str, endpoint: str) -> ExternalKnowledgeApis | None:
    """The tenant's existing ExternalKnowledgeApis row pointing at the kernel endpoint, if any."""
    rows = db.session.scalars(select(ExternalKnowledgeApis).where(ExternalKnowledgeApis.tenant_id == tenant_id)).all()
    for row in rows:
        if not row.settings:
            continue
        try:
            settings = json.loads(row.settings)
        except ValueError:
            continue
        candidate = str(settings.get("endpoint") or "").strip().rstrip("/")
        if candidate == endpoint:
            return row
    return None
