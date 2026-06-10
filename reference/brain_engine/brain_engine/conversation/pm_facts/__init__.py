"""PM-provided knowledge facts — durable memory of manager corrections.

Captures every manager reply that fills a knowledge gap (WiFi
password, parking rules, late-checkout decisions, …) so the next
guest message gets the answer instead of repeating the BRAIN flag.

Public surface intentionally narrow:

* :class:`PmFact` — value object that lands in storage.
* :class:`PmFactStore` — runtime-checkable Protocol.
* :class:`InMemoryPmFactStore` — dev / unit-test default.
* :class:`PgPmFactStore` — production implementation, asyncpg-backed.
* :func:`create_pm_facts_pool` — pool helper mirroring the sandbox /
  property profile stores, so lifespan wiring stays uniform.
"""

from __future__ import annotations

from brain_engine.conversation.pm_facts.models import PmFact
from brain_engine.conversation.pm_facts.postgres_store import (
    PgPmFactStore,
    create_pm_facts_pool,
)
from brain_engine.conversation.pm_facts.relevance import (
    PmFactRelevanceStats,
    compute_pm_fact_relevance_stats,
    log_pm_fact_relevance,
)
from brain_engine.conversation.pm_facts.store import (
    InMemoryPmFactStore,
    PmFactStore,
)

__all__ = [
    "InMemoryPmFactStore",
    "PgPmFactStore",
    "PmFact",
    "PmFactRelevanceStats",
    "PmFactStore",
    "compute_pm_fact_relevance_stats",
    "create_pm_facts_pool",
    "log_pm_fact_relevance",
]
