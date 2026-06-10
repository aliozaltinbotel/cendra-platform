"""Lifespan wiring for the A/B :class:`ExperimentRegistry`.

Backend selection is delegated to
:func:`brain_engine.experiments.wiring.build_experiment_store`,
which reads ``EXPERIMENT_STORE_BACKEND`` and the matching
connection knobs from the environment.

The store and the registry are constructed together because they
share a lifecycle: a registry without a store loses its tally on
every pod rollout (the bug this branch closes), and a store
without a registry has no in-process consumer for outcomes.

A failure to construct the store is non-fatal by design: the
runtime can still serve experiments through an in-process
:class:`InMemoryExperimentStore`, just without survival across
restarts.  Operators see the warning and roll out the migration
before flipping the backend selector.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from brain_engine.experiments.ab_test_engine import ExperimentRegistry
from brain_engine.experiments.store import (
    ExperimentStore,
    InMemoryExperimentStore,
)
from brain_engine.experiments.wiring import (
    CloseCallable,
    build_experiment_store,
)

logger = logging.getLogger(__name__)


async def wire(
    application: FastAPI,
) -> tuple[
    ExperimentStore,
    CloseCallable | None,
    ExperimentRegistry,
]:
    """Construct the experiment store + registry, attach to app state.

    On success ``application.state.experiment_store`` and
    ``application.state.experiment_registry`` are populated.  Any
    persisted experiments are warm-loaded so the registry comes up
    with a populated tally before the first request lands.

    On a documented backend failure the function logs a warning
    and falls back to an in-memory store so the runtime stays
    responsive while operators investigate.

    Args:
        application: The FastAPI app whose ``state`` is the
            canonical home for the constructed store + registry.

    Returns:
        A tuple ``(store, close, registry)``.  ``close`` must be
        awaited on shutdown to release any pool the factory
        owned; it is ``None`` for the in-memory fallback.
    """
    try:
        store, close = await build_experiment_store()
    except (ValueError, OSError, ConnectionError) as exc:
        logger.warning(
            "Experiment store init failed — falling back to "
            "in-memory: %s",
            exc,
        )
        store = InMemoryExperimentStore()
        close = None

    registry = ExperimentRegistry(store=store)

    try:
        restored = await registry.warm_from_store()
    except (OSError, ConnectionError) as exc:
        logger.warning(
            "Experiment warm-up failed — registry starts empty: %s",
            exc,
        )
        restored = 0

    application.state.experiment_store = store
    application.state.experiment_registry = registry
    logger.info(
        "Experiment registry initialized (restored=%d)",
        restored,
    )
    return store, close, registry
