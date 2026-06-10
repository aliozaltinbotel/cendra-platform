"""Nightly memory-consolidation Celery task (Cendra brain kernel).

Batch 3 ships the kernel pipeline (``core.brain.memory.memory_consolidator``
— episodic → semantic/KG distillation with surprise gating, plus
episodic dedup, recency decay and contradiction detection).  This task
is the scheduling surface for it: the beat_schedule entry is touchpoint
T5 (``api/extensions/ext_celery.py``, Batch 5), and the per-tenant
store/embedder wiring arrives with the runtime adapters in Batch 4/5 —
until then the task logs and returns so an accidental invocation is
harmless.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="dataset")
def brain_memory_consolidation_task() -> None:
    """Run the nightly brain memory consolidation across tenants.

    Wiring lands with Batch 4/5 (T5 beat entry + tenant-scoped stores
    and the embedding-pod client); the task is a deliberate no-op until
    then.
    """
    logger.info("brain_memory_consolidation_task: runtime wiring lands in Batch 4/5 — skipping")
