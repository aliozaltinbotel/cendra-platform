"""HTTP surface for the A/B :class:`ExperimentRegistry`.

Three endpoints under ``/api/admin/experiments``:

* ``POST /``                      — register a new experiment.
* ``POST /{id}/outcomes``         — record one outcome row.
* ``GET  /{id}/verdict``          — return the current verdict.

Writes go through the registry's *durable* methods
(:meth:`ExperimentRegistry.register_persisted` /
:meth:`ExperimentRegistry.record_outcome_persisted`) so that every
mutation is mirrored to the attached
:class:`brain_engine.experiments.store.ExperimentStore`.  Reads
serve the in-memory snapshot directly — verdicts are pure compute
and do not need a round trip to Postgres.

The router is wired via :mod:`api_server.bootstrap.experiments` at
application startup; the registry is published into ``app.state``
and surfaced here through :func:`configure_deps`, mirroring the
shape used by :mod:`api_server.routers.memory_status`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from brain_engine.experiments.ab_test_engine import (
    Experiment,
    ExperimentRegistry,
    ExperimentVerdict,
    Variant,
    VariantOutcome,
)

__all__ = ["configure_deps", "router"]


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/admin/experiments",
    tags=["Intelligence"],
)


_deps: dict[str, Any] = {"registry": None}


def configure_deps(deps: dict[str, Any]) -> None:
    """Publish lifespan-built dependencies into the router scope.

    Re-entrant: a second call replaces the prior values atomically
    so test fixtures can swap stacks between runs.
    """
    _deps.update(deps)


# --------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------- #


class _VariantPayload(BaseModel):
    """One variant of an experiment registration request."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=128)
    weight: float = Field(ge=0.0)
    is_control: bool = False


class RegisterExperimentRequest(BaseModel):
    """Payload accepted by ``POST /api/admin/experiments``."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1, max_length=128)
    variants: list[_VariantPayload] = Field(min_length=2)
    name: str = ""
    hypothesis: str = ""
    salt: str = ""
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    min_trials_per_arm: int = Field(default=50, ge=1)


class RecordOutcomeRequest(BaseModel):
    """Payload accepted by ``POST .../outcomes``."""

    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(min_length=1, max_length=128)
    success: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------- #


@router.post(
    "",
    summary="Register a new A/B experiment",
    status_code=status.HTTP_201_CREATED,
)
async def register_experiment(
    payload: RegisterExperimentRequest,
) -> JSONResponse:
    """Register an experiment and persist it through the store.

    Returns ``201`` with the resolved control id and the persisted
    variant matrix.  ``409`` is returned when the experiment id is
    already registered — the registry treats double registration
    as a misconfiguration rather than an idempotent no-op.
    """
    registry = _require_registry()
    variants = tuple(
        Variant(
            variant_id=v.variant_id,
            weight=v.weight,
            is_control=v.is_control,
        )
        for v in payload.variants
    )
    try:
        experiment = Experiment(
            experiment_id=payload.experiment_id,
            variants=variants,
            salt=payload.salt,
            alpha=payload.alpha,
            min_trials_per_arm=payload.min_trials_per_arm,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        await registry.register_persisted(
            experiment,
            name=payload.name,
            hypothesis=payload.hypothesis,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "experiment_id": experiment.experiment_id,
            "control_id": experiment.control_id,
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "weight": v.weight,
                    "is_control": v.is_control,
                }
                for v in experiment.variants
            ],
            "alpha": experiment.alpha,
            "min_trials_per_arm": experiment.min_trials_per_arm,
        },
    )


@router.post(
    "/{experiment_id}/outcomes",
    summary="Record an outcome for one experiment variant",
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_outcome(
    payload: RecordOutcomeRequest,
    experiment_id: str = Path(..., min_length=1, max_length=128),
) -> JSONResponse:
    """Append an outcome row to the durable ledger.

    Returns ``202`` once both the in-memory tally and the durable
    write have completed.  ``404`` is returned when the experiment
    is not registered, ``422`` when the variant id is unknown.
    """
    registry = _require_registry()
    try:
        await registry.record_outcome_persisted(
            experiment_id,
            payload.variant_id,
            success=payload.success,
            metadata=payload.metadata,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "experiment_id": experiment_id,
            "variant_id": payload.variant_id,
            "success": payload.success,
        },
    )


@router.get(
    "/{experiment_id}/verdict",
    summary="Return the current verdict for an experiment",
)
async def get_verdict(
    experiment_id: str = Path(..., min_length=1, max_length=128),
) -> JSONResponse:
    """Compute and return the current :class:`ExperimentVerdict`.

    The verdict is recomputed from the in-memory tally on every
    call — the math is cheap and the registry guarantees the
    tally is consistent with the durable ledger after warm-up.
    """
    registry = _require_registry()
    try:
        verdict = registry.verdict(experiment_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return JSONResponse(content=_verdict_to_dict(verdict))


# --------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------- #


def _require_registry() -> ExperimentRegistry:
    """Return the configured registry or raise ``503``."""
    registry = _deps.get("registry")
    if not isinstance(registry, ExperimentRegistry):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="experiment registry not initialized",
        )
    return registry


def _verdict_to_dict(verdict: ExperimentVerdict) -> dict[str, Any]:
    """Serialise an :class:`ExperimentVerdict` for JSON output."""
    return {
        "experiment_id": verdict.experiment_id,
        "ready": verdict.ready,
        "winner": verdict.winner,
        "outcomes": {
            variant_id: _outcome_to_dict(outcome)
            for variant_id, outcome in verdict.outcomes.items()
        },
        "comparisons": {
            variant_id: {
                "lift": comp.lift,
                "p_value": comp.p_value,
                "significant": comp.significant,
                "z_score": comp.z_score,
            }
            for variant_id, comp in verdict.comparisons.items()
        },
    }


def _outcome_to_dict(outcome: VariantOutcome) -> dict[str, Any]:
    """Serialise a :class:`VariantOutcome` for JSON output."""
    return {
        "variant_id": outcome.variant_id,
        "trials": outcome.trials,
        "successes": outcome.successes,
        "conversion_rate": outcome.conversion_rate,
    }
