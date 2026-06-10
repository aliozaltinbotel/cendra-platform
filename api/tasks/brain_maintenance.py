"""Nightly brain maintenance Celery tasks (Cendra, Batch 5).

Scheduling surface for the T5 beat entries (FORK_LEDGER.md).  Each task
is tenant-iterating work that needs the service-layer store wiring; the
pieces that cannot run yet log and return so an early beat activation
is harmless.  brain_memory_consolidation_task lives in
tasks/brain_consolidation.py.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="dataset")
def brain_pattern_mining_task() -> None:
    """Nightly pattern mining over captured DecisionCases (per tenant).

    Tenant fan-out + scenario classification arrive with the service
    layer; until a tenant has classified (non-'general') cases the
    miner has nothing learnable, so this task only reports readiness.
    """
    logger.info("brain_pattern_mining_task: awaiting classified cases / tenant fan-out — skipping")


@shared_task(queue="dataset")
def brain_autonomy_eval_task() -> None:
    """Per-workflow autonomy promotion/demotion sweep (MetricsCollector.flush)."""
    logger.info("brain_autonomy_eval_task: awaiting interaction-source wiring — skipping")


@shared_task(queue="dataset")
def brain_friction_decay_task() -> None:
    """Apply recency decay to stored facts (memory tier maintenance)."""
    logger.info("brain_friction_decay_task: awaiting per-tenant fact-store wiring — skipping")
