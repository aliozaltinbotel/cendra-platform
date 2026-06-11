"""Nightly sleep-time consolidation Celery task (Cendra, Batch 6).

Scheduling surface for the cognition sleep loop
(core/brain/cognition/sleep.py — Letta-style nightly distillation of
the day's ResolvedDecisions into a playbook delta).  Tenant fan-out
over captured decisions arrives with the continual-learning service
wiring; until then the task logs and returns.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="dataset")
def brain_sleep_consolidation_task() -> None:
    """Run nightly sleep-time playbook distillation across tenants."""
    logger.info("brain_sleep_consolidation_task: awaiting tenant decision-feed wiring — skipping")
