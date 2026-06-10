"""Experiment registry + verdict pipeline.

Wires :mod:`brain_engine.experiments.traffic_splitter` and
:mod:`brain_engine.experiments.statistical_significance` together
into a small, in-memory experiment surface.  Durability — running
experiments across processes, persisting outcomes — is the
runtime tier's job; this module only owns *the math*.

Lifecycle:

1. Build :class:`Variant` rows with weights.
2. Wrap them in an :class:`Experiment`.
3. Pass the experiment to :class:`ExperimentRegistry.register`.
4. For every subject, ``registry.assign(exp_id, subject_id)``
   returns the variant id.  The registry never mutates state on
   assignment — it is safe to call from any thread.
5. Outcomes (success / failure) are reported back through
   :meth:`ExperimentRegistry.record_outcome`.
6. :meth:`ExperimentRegistry.verdict` runs the z-test against a
   pinned control variant and returns a verdict.

The registry keeps tallies in plain dicts; persistence /
sharding is out of scope.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from brain_engine.experiments.statistical_significance import (
    DEFAULT_ALPHA,
    SignificanceResult,
    two_proportion_z_test,
)
from brain_engine.experiments.traffic_splitter import (
    DeterministicTrafficSplitter,
    SplitDecision,
    TrafficSplit,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from brain_engine.experiments.store import ExperimentStore

__all__ = [
    "DEFAULT_MIN_TRIALS_PER_ARM",
    "Experiment",
    "ExperimentRegistry",
    "ExperimentVerdict",
    "Variant",
    "VariantOutcome",
]


DEFAULT_MIN_TRIALS_PER_ARM: Final[int] = 50
"""Floor on per-variant sample size before the registry is
willing to publish a verdict — protects against early-stop
illusions on tiny samples."""


@dataclass(frozen=True, slots=True)
class Variant:
    """One arm of an experiment.

    Attributes:
        variant_id: Stable identifier.
        weight: Allocation share — fed verbatim to the
            traffic splitter and normalised there.
        is_control: Exactly one variant per experiment must be
            the control (baseline) arm.
    """

    variant_id: str
    weight: float
    is_control: bool = False

    def __post_init__(self) -> None:
        if not self.variant_id:
            raise ValueError("variant_id must not be empty")
        if self.weight < 0:
            raise ValueError("weight must be >= 0")


@dataclass(frozen=True, slots=True)
class Experiment:
    """A registered A/B experiment.

    Attributes:
        experiment_id: Globally unique id.
        variants: Tuple of :class:`Variant` rows; exactly one
            ``is_control`` must be ``True``.
        salt: Per-experiment salt for the splitter.  Defaults to
            ``experiment_id`` so independent experiments are
            decorrelated by default.
        alpha: Significance level for verdicts.
        min_trials_per_arm: Floor on per-arm sample size.
    """

    experiment_id: str
    variants: tuple[Variant, ...]
    salt: str = ""
    alpha: float = DEFAULT_ALPHA
    min_trials_per_arm: int = DEFAULT_MIN_TRIALS_PER_ARM

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("experiment_id must not be empty")
        if len(self.variants) < 2:
            raise ValueError(
                "experiment must define at least two variants",
            )
        controls = [v for v in self.variants if v.is_control]
        if len(controls) != 1:
            raise ValueError(
                "experiment must define exactly one control variant",
            )
        if sum(v.weight for v in self.variants) <= 0:
            raise ValueError(
                "at least one variant must have weight > 0",
            )
        if not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must lie in (0, 1)")
        if self.min_trials_per_arm < 1:
            raise ValueError("min_trials_per_arm must be >= 1")

    @property
    def control_id(self) -> str:
        for variant in self.variants:
            if variant.is_control:
                return variant.variant_id
        # Cannot happen — guarded in __post_init__.
        raise AssertionError("experiment has no control variant")


@dataclass(frozen=True, slots=True)
class VariantOutcome:
    """Aggregated outcomes for one variant.

    Attributes:
        variant_id: Variant the tally belongs to.
        trials: Total recorded outcomes.
        successes: Successful outcomes (treatment positive).
    """

    variant_id: str
    trials: int
    successes: int

    def __post_init__(self) -> None:
        if self.trials < 0:
            raise ValueError("trials must be >= 0")
        if self.successes < 0:
            raise ValueError("successes must be >= 0")
        if self.successes > self.trials:
            raise ValueError("successes cannot exceed trials")

    @property
    def conversion_rate(self) -> float:
        if self.trials == 0:
            return 0.0
        return self.successes / self.trials


@dataclass(frozen=True, slots=True)
class ExperimentVerdict:
    """Final verdict from :meth:`ExperimentRegistry.verdict`.

    Attributes:
        experiment_id: Experiment the verdict refers to.
        outcomes: Per-variant outcome rows.
        comparisons: For each non-control variant, the z-test
            against the control.  Keyed by ``variant_id``.
        ready: ``True`` once every variant has at least
            ``min_trials_per_arm`` trials.
        winner: Variant id of the strictly best significant arm,
            or ``None`` if none reached significance with a
            positive lift over control.
    """

    experiment_id: str
    outcomes: Mapping[str, VariantOutcome]
    comparisons: Mapping[str, SignificanceResult]
    ready: bool
    winner: str | None


class ExperimentRegistry:
    """Experiment registry with optional durable persistence.

    The hot read path stays in-process — variant assignment and
    verdict math run against in-memory tallies for latency.  When
    a :class:`brain_engine.experiments.store.ExperimentStore` is
    injected, every mutator is mirrored to the store so the
    registry survives pod rollouts.

    Without a store, the registry behaves exactly like the
    original in-memory implementation: tests and offline tools
    do not need Postgres to reach a verdict.
    """

    def __init__(
        self,
        *,
        store: ExperimentStore | None = None,
    ) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._splitters: dict[str, DeterministicTrafficSplitter] = {}
        self._splits: dict[str, TrafficSplit] = {}
        self._tally: defaultdict[
            tuple[str, str], list[int]
        ] = defaultdict(lambda: [0, 0])  # [trials, successes]
        self._store = store

    @property
    def store(self) -> ExperimentStore | None:
        """Underlying durable store, if one was injected."""
        return self._store

    def register(self, experiment: Experiment) -> None:
        if experiment.experiment_id in self._experiments:
            raise ValueError(
                f"experiment {experiment.experiment_id!r} "
                f"already registered",
            )
        salt = experiment.salt or experiment.experiment_id
        weights = {
            v.variant_id: v.weight for v in experiment.variants
        }
        self._experiments[experiment.experiment_id] = experiment
        self._splitters[experiment.experiment_id] = (
            DeterministicTrafficSplitter(salt=salt)
        )
        self._splits[experiment.experiment_id] = TrafficSplit(
            weights=weights,
        )

    def list_experiments(self) -> Sequence[Experiment]:
        return tuple(self._experiments.values())

    def assign(
        self,
        experiment_id: str,
        subject_id: str,
    ) -> SplitDecision:
        try:
            splitter = self._splitters[experiment_id]
            split = self._splits[experiment_id]
        except KeyError as exc:
            raise KeyError(
                f"experiment {experiment_id!r} is not registered",
            ) from exc
        return splitter.assign(subject_id, split)

    def record_outcome(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        success: bool,
    ) -> None:
        if experiment_id not in self._experiments:
            raise KeyError(
                f"experiment {experiment_id!r} is not registered",
            )
        valid = {
            v.variant_id
            for v in self._experiments[experiment_id].variants
        }
        if variant_id not in valid:
            raise KeyError(
                f"variant {variant_id!r} not part of "
                f"{experiment_id!r}",
            )
        slot = self._tally[(experiment_id, variant_id)]
        slot[0] += 1
        if success:
            slot[1] += 1

    def outcomes(
        self,
        experiment_id: str,
    ) -> Mapping[str, VariantOutcome]:
        try:
            experiment = self._experiments[experiment_id]
        except KeyError as exc:
            raise KeyError(
                f"experiment {experiment_id!r} is not registered",
            ) from exc
        result: dict[str, VariantOutcome] = {}
        for variant in experiment.variants:
            trials, successes = self._tally.get(
                (experiment_id, variant.variant_id),
                (0, 0),
            )
            result[variant.variant_id] = VariantOutcome(
                variant_id=variant.variant_id,
                trials=trials,
                successes=successes,
            )
        return result

    def verdict(self, experiment_id: str) -> ExperimentVerdict:
        try:
            experiment = self._experiments[experiment_id]
        except KeyError as exc:
            raise KeyError(
                f"experiment {experiment_id!r} is not registered",
            ) from exc
        outcomes = self.outcomes(experiment_id)
        control_id = experiment.control_id
        control = outcomes[control_id]
        comparisons: dict[str, SignificanceResult] = {}
        for variant in experiment.variants:
            if variant.variant_id == control_id:
                continue
            challenger = outcomes[variant.variant_id]
            comparisons[variant.variant_id] = two_proportion_z_test(
                successes_a=control.successes,
                trials_a=control.trials,
                successes_b=challenger.successes,
                trials_b=challenger.trials,
                alpha=experiment.alpha,
            )
        ready = all(
            o.trials >= experiment.min_trials_per_arm
            for o in outcomes.values()
        )
        winner = self._pick_winner(
            ready=ready,
            outcomes=outcomes,
            comparisons=comparisons,
        )
        return ExperimentVerdict(
            experiment_id=experiment_id,
            outcomes=outcomes,
            comparisons=comparisons,
            ready=ready,
            winner=winner,
        )

    # ── Durable mutators ─────────────────────────────────── #

    async def register_persisted(
        self,
        experiment: Experiment,
        *,
        name: str = "",
        hypothesis: str = "",
        status: str = "running",
    ) -> None:
        """Register ``experiment`` and persist it through the store.

        Falls back to plain :meth:`register` when no store is
        attached, so call sites that always go through this method
        keep working in tests / offline tools.

        Args:
            experiment: The experiment definition to register.
            name: Human-readable name (analyst tooling).
            hypothesis: Free-form hypothesis text.
            status: Lifecycle status (``running`` / ``stopped``).
        """
        self.register(experiment)
        if self._store is None:
            return
        save = getattr(self._store, "save_experiment", None)
        if save is None:
            return
        # Pg implementation accepts metadata kwargs; the Protocol
        # signature does not require them, so we feature-detect.
        try:
            await save(
                experiment,
                name=name,
                hypothesis=hypothesis,
                status=status,
            )
        except TypeError:
            await save(experiment)

    async def record_outcome_persisted(
        self,
        experiment_id: str,
        variant_id: str,
        *,
        success: bool,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record an outcome and append it to the durable ledger."""
        self.record_outcome(
            experiment_id,
            variant_id,
            success=success,
        )
        if self._store is None:
            return
        await self._store.record_outcome(
            experiment_id,
            variant_id,
            success=success,
            metadata=metadata,
        )

    async def warm_from_store(self) -> int:
        """Re-hydrate registry state from the attached store.

        Returns the number of experiments restored.  Each
        experiment also gets its in-memory tally repopulated from
        the persisted aggregates so verdicts are reproducible
        across pod restarts.

        Returns:
            Number of experiments loaded from the store.
        """
        if self._store is None:
            return 0
        experiments = await self._store.load_experiments()
        restored = 0
        for experiment in experiments:
            if experiment.experiment_id in self._experiments:
                # Already registered (idempotent warm-up).
                continue
            self.register(experiment)
            aggregates = await self._store.load_aggregates(
                experiment.experiment_id,
            )
            for variant_id, (trials, successes) in aggregates.items():
                slot = self._tally[
                    (experiment.experiment_id, variant_id)
                ]
                slot[0] = int(trials)
                slot[1] = int(successes)
            restored += 1
        return restored

    @staticmethod
    def _pick_winner(
        *,
        ready: bool,
        outcomes: Mapping[str, VariantOutcome],
        comparisons: Mapping[str, SignificanceResult],
    ) -> str | None:
        if not ready:
            return None
        best: tuple[float, str] | None = None
        for variant_id, comp in comparisons.items():
            if not comp.significant or comp.lift <= 0.0:
                continue
            rate = outcomes[variant_id].conversion_rate
            if best is None or rate > best[0]:
                best = (rate, variant_id)
        return best[1] if best is not None else None
