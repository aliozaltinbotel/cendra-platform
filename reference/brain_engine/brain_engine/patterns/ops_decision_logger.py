"""Ops-autonomy :class:`DecisionCase` emitter — Gap #1 of the global scope.

Cleaner fallback chains, vendor dispatches, vendor negotiations and
quality-acceptance checks are first-class operational decisions.
Until now they ran without leaving a trace in the learning subsystem,
so the pattern extractor was blind to them.  This module provides a
small, reusable façade that turns each ops event into a
:class:`~brain_engine.patterns.models.DecisionCase` and hands it to
the injected :class:`~brain_engine.patterns.store.DecisionCaseStore`.

Design contract:

* The logger never raises.  An ops pipeline must stay up even if the
  store is down, so every persistence error is logged and swallowed.
* The logger is a no-op when ``case_store`` is ``None``.  This keeps
  call sites symmetric with :class:`ConversationService`, which
  already treats a missing store as "disabled learning".
* Every method takes keyword arguments.  Positional-only call sites
  would be too fragile for ops code that accumulates context
  incrementally (dispatch → negotiation → acceptance).

Adding a new ops scenario is a two-step change: extend
:class:`~brain_engine.patterns.models.Scenario` and add a single
method here that packs the scenario-specific ``ops_snapshot`` keys.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)

if TYPE_CHECKING:
    from brain_engine.fallback.fallback_chain import FallbackResult
    from brain_engine.patterns.store import DecisionCaseStore

logger = logging.getLogger(__name__)


class OpsDecisionLogger:
    """Record ops-autonomy DecisionCases without ever breaking the flow.

    The logger is deliberately thin: it owns no threads, no queues,
    and no retries.  Persistence durability is the store's problem;
    the logger's single job is to shape a DecisionCase correctly and
    hand it over.

    Attributes:
        _case_store: Optional store; when ``None`` every method is a
            no-op so ops code does not need to branch on its own.
    """

    def __init__(self, case_store: DecisionCaseStore | None) -> None:
        self._case_store = case_store

    async def log_cleaner_dispatch(
        self,
        *,
        property_id: str,
        owner_id: str,
        fallback_result: FallbackResult,
        reservation_id: str | None = None,
    ) -> str | None:
        """Record the outcome of a cleaner fallback chain.

        The case's decision is :class:`DecisionType.DISPATCH` when the
        chain resolved, :class:`DecisionType.ESCALATE` otherwise — the
        latter captures "every cleaner said no, a human must decide".

        Args:
            property_id: Property whose cleaning was being scheduled.
            owner_id: Owner of that property.
            fallback_result: Result object from
                :meth:`FallbackChain.execute`.
            reservation_id: Reservation the cleaning was attached to,
                if any.

        Returns:
            ``case_id`` of the stored case, or ``None`` when the store
            is disabled or a persistence error was swallowed.
        """
        resolved = bool(fallback_result.resolved)
        action_type = DecisionType.DISPATCH if resolved else DecisionType.ESCALATE
        action_params: dict[str, Any] = {
            "successful_step": fallback_result.successful_step,
            "steps_attempted": fallback_result.steps_attempted,
            "total_steps": fallback_result.total_steps,
        }
        outcome = CaseOutcome(
            successful=resolved,
            resolution_type=(
                ResolutionType.AUTO_RESOLVED
                if resolved
                else ResolutionType.ESCALATED
            ),
        )
        ops_snapshot: dict[str, Any] = {
            "fallback_chain": "cleaner_dispatch",
            "step_details": list(fallback_result.step_details),
        }
        return await self._store(
            scenario=Scenario.CLEANER_DISPATCH,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            decision=DecisionAction(
                action_type=action_type, params=action_params,
            ),
            outcome=outcome,
            ops_snapshot=ops_snapshot,
        )

    async def log_vendor_dispatch(
        self,
        *,
        property_id: str,
        owner_id: str,
        vendor_name: str,
        resolved: bool,
        reservation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str | None:
        """Record a single vendor dispatch attempt.

        Vendor dispatches are not currently chained, so the signal is
        binary: the vendor accepted the job or they did not.

        Args:
            property_id: Property receiving the vendor.
            owner_id: Owner of the property.
            vendor_name: Identifier of the dispatched vendor.
            resolved: Whether the vendor accepted the job.
            reservation_id: Linked reservation if applicable.
            details: Extra context to persist in ``ops_snapshot``
                (issue type, severity, target time, …).

        Returns:
            ``case_id`` of the stored case, or ``None``.
        """
        action_type = (
            DecisionType.DISPATCH if resolved else DecisionType.ESCALATE
        )
        ops_snapshot: dict[str, Any] = {
            "vendor_name": vendor_name,
            "resolved": resolved,
        }
        if details:
            ops_snapshot.update(details)
        outcome = CaseOutcome(
            successful=resolved,
            resolution_type=(
                ResolutionType.AUTO_RESOLVED
                if resolved
                else ResolutionType.ESCALATED
            ),
        )
        return await self._store(
            scenario=Scenario.VENDOR_DISPATCH,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            decision=DecisionAction(
                action_type=action_type,
                params={"vendor_name": vendor_name},
            ),
            outcome=outcome,
            ops_snapshot=ops_snapshot,
        )

    async def log_vendor_negotiation(
        self,
        *,
        property_id: str,
        owner_id: str,
        vendor_name: str,
        accepted: bool,
        proposal: dict[str, Any] | None = None,
        reservation_id: str | None = None,
    ) -> str | None:
        """Record the outcome of a time/price negotiation with a vendor.

        ``proposal`` is the vendor's offer (``{"time": ..., "price":
        ..., ...}``).  ``accepted`` is the engine's decision — stored
        as APPROVE/DENY so later pattern extraction can correlate
        acceptance with offer attributes.

        Args:
            property_id: Property this negotiation applies to.
            owner_id: Owner of the property.
            vendor_name: Vendor on the other side of the negotiation.
            accepted: Whether the engine accepted the vendor's offer.
            proposal: Offer terms kept verbatim for learning.
            reservation_id: Linked reservation if applicable.

        Returns:
            ``case_id`` of the stored case, or ``None``.
        """
        action_type = (
            DecisionType.APPROVE if accepted else DecisionType.DENY
        )
        ops_snapshot: dict[str, Any] = {
            "vendor_name": vendor_name,
            "proposal": dict(proposal) if proposal else {},
            "accepted": accepted,
        }
        outcome = CaseOutcome(
            successful=accepted,
            resolution_type=(
                ResolutionType.AUTO_RESOLVED
                if accepted
                else ResolutionType.ESCALATED
            ),
        )
        return await self._store(
            scenario=Scenario.VENDOR_NEGOTIATION,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            decision=DecisionAction(
                action_type=action_type,
                params={
                    "vendor_name": vendor_name,
                    "proposal": dict(proposal) if proposal else {},
                },
            ),
            outcome=outcome,
            ops_snapshot=ops_snapshot,
        )

    async def log_quality_acceptance(
        self,
        *,
        property_id: str,
        owner_id: str,
        contractor_name: str,
        accepted: bool,
        reason: str = "",
        reservation_id: str | None = None,
    ) -> str | None:
        """Record an accept/reject decision on completed work.

        Quality acceptance is the closing event of any ops coordination
        (cleaner or vendor).  Rejection is a valuable learning signal
        because it identifies contractors whose work tends to require
        re-work.

        Args:
            property_id: Property where the work was done.
            owner_id: Owner of the property.
            contractor_name: Cleaner or vendor being evaluated.
            accepted: Whether the work passed inspection.
            reason: Free-text justification — useful for later NLP
                feature extraction.
            reservation_id: Linked reservation if applicable.

        Returns:
            ``case_id`` of the stored case, or ``None``.
        """
        action_type = (
            DecisionType.APPROVE if accepted else DecisionType.DENY
        )
        ops_snapshot: dict[str, Any] = {
            "contractor_name": contractor_name,
            "accepted": accepted,
            "reason": reason,
        }
        outcome = CaseOutcome(
            successful=accepted,
            resolution_type=(
                ResolutionType.AUTO_RESOLVED
                if accepted
                else ResolutionType.PM_DENIED
            ),
        )
        return await self._store(
            scenario=Scenario.QUALITY_ACCEPTANCE,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            decision=DecisionAction(
                action_type=action_type,
                params={
                    "contractor_name": contractor_name,
                    "reason": reason,
                },
            ),
            outcome=outcome,
            ops_snapshot=ops_snapshot,
        )

    # -- internal -----------------------------------------------------

    async def _store(
        self,
        *,
        scenario: Scenario,
        property_id: str,
        owner_id: str,
        reservation_id: str | None,
        decision: DecisionAction,
        outcome: CaseOutcome,
        ops_snapshot: dict[str, Any],
    ) -> str | None:
        """Build the case, hand it to the store, and swallow errors.

        All public ``log_*`` methods share the same final step.  Keeping
        it here means each scenario-specific method stays focused on
        what makes it unique: its snapshot and action parameters.
        """
        if self._case_store is None:
            return None
        case = DecisionCase(
            stage=BookingStage.OPS,
            scenario=scenario,
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            decision=decision,
            outcome=outcome,
            ops_snapshot=ops_snapshot,
        )
        try:
            return await self._case_store.store(case)
        except Exception:
            logger.exception(
                "Ops DecisionCase persistence failed (scenario=%s, "
                "property=%s) — swallowing to keep ops flow alive",
                scenario.value, property_id,
            )
            return None
