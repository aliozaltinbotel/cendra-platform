"""Vendor dispatch and payment tracking for property management.

Handles dispatching repair vendors (TV replacement, plumber, etc.),
tracking their status, and managing payments for cleaners and vendors.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain_engine.negotiation.models import (
        NegotiationOffer,
        NegotiationOutcome,
        NegotiationTarget,
    )
    from brain_engine.negotiation.orchestrator import Negotiator
    from brain_engine.patterns.ops_decision_logger import OpsDecisionLogger

logger = logging.getLogger(__name__)


@dataclass
class VendorInfo:
    """A registered vendor/contractor."""
    vendor_id: str = ""
    name: str = ""
    phone: str = ""
    specialty: str = ""  # plumber, electrician, tv_repair, locksmith, general
    hourly_rate: float = 0.0
    rating: float = 0.0
    available: bool = True

    def __post_init__(self) -> None:
        if not self.vendor_id:
            self.vendor_id = f"VND-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class DispatchOrder:
    """A vendor dispatch/work order."""
    order_id: str = ""
    vendor_id: str = ""
    property_id: str = ""
    task_description: str = ""
    status: str = "pending"  # pending, dispatched, en_route, on_site, completed, cancelled
    created_at: str = ""
    scheduled_for: str = ""
    completed_at: str = ""
    cost_estimate: float = 0.0
    final_cost: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.order_id:
            self.order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class PaymentRecord:
    """A payment to a cleaner or vendor."""
    payment_id: str = ""
    recipient_name: str = ""
    recipient_type: str = ""  # cleaner, vendor, contractor
    amount: float = 0.0
    currency: str = "USD"
    status: str = "pending"  # pending, approved, paid, failed
    description: str = ""
    created_at: str = ""
    paid_at: str = ""

    def __post_init__(self) -> None:
        if not self.payment_id:
            self.payment_id = f"PAY-{uuid.uuid4().hex[:8].upper()}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class VendorDispatcher:
    """Manages vendor registry and dispatch orders.

    Optionally accepts an :class:`OpsDecisionLogger` so every dispatch
    attempt is persisted as a DecisionCase.  Without a logger the
    dispatcher behaves exactly as before — ops-autonomy logging is
    additive, never a pre-condition.
    """

    def __init__(
        self,
        ops_logger: OpsDecisionLogger | None = None,
    ) -> None:
        self._vendors: dict[str, VendorInfo] = {}
        self._orders: dict[str, DispatchOrder] = {}
        self._ops_logger = ops_logger

    def register_vendor(self, vendor: VendorInfo) -> None:
        self._vendors[vendor.vendor_id] = vendor
        logger.info("Vendor registered: %s (%s)", vendor.name, vendor.specialty)

    def find_vendors(self, specialty: str) -> list[VendorInfo]:
        """Find available vendors by specialty."""
        return [
            v for v in self._vendors.values()
            if v.specialty == specialty and v.available
        ]

    async def dispatch(
        self,
        property_id: str,
        task_description: str,
        specialty: str,
        scheduled_for: str = "",
        owner_id: str = "",
        reservation_id: str | None = None,
    ) -> DispatchOrder:
        """Create and dispatch a work order to the best available vendor.

        Args:
            property_id: Property the vendor is being dispatched to.
            task_description: Human-readable job description.
            specialty: Vendor speciality (plumber, electrician, …).
            scheduled_for: ISO timestamp for the scheduled visit.
            owner_id: Owner of the property — required for ops
                DecisionCase emission; when empty the logger call is
                skipped because scope-based learning needs owner_id.
            reservation_id: Linked reservation if applicable.

        Returns:
            The :class:`DispatchOrder` representing the attempt.  The
            ``status`` field is ``"dispatched"`` on success or
            ``"no_vendor_available"`` on failure.
        """
        vendors = self.find_vendors(specialty)
        if not vendors:
            order = DispatchOrder(
                property_id=property_id,
                task_description=task_description,
                status="no_vendor_available",
            )
            self._orders[order.order_id] = order
            logger.warning("No vendor available for specialty: %s", specialty)
            await self._emit_dispatch_case(
                property_id=property_id,
                owner_id=owner_id,
                reservation_id=reservation_id,
                vendor_name="",
                resolved=False,
                details={
                    "specialty": specialty,
                    "task_description": task_description,
                    "reason": "no_vendor_available",
                },
            )
            return order

        # Pick highest-rated available vendor
        vendor = max(vendors, key=lambda v: v.rating)

        order = DispatchOrder(
            vendor_id=vendor.vendor_id,
            property_id=property_id,
            task_description=task_description,
            status="dispatched",
            scheduled_for=scheduled_for,
            cost_estimate=vendor.hourly_rate * 2,  # Estimate 2 hours
        )
        self._orders[order.order_id] = order

        logger.info(
            "Dispatched %s to %s for '%s' at property %s",
            vendor.name, order.order_id, task_description, property_id,
        )
        await self._emit_dispatch_case(
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            vendor_name=vendor.name,
            resolved=True,
            details={
                "specialty": specialty,
                "task_description": task_description,
                "vendor_id": vendor.vendor_id,
                "cost_estimate": order.cost_estimate,
                "scheduled_for": scheduled_for,
            },
        )
        return order

    async def negotiate_and_dispatch(
        self,
        *,
        vendor: VendorInfo,
        property_id: str,
        task_description: str,
        target: NegotiationTarget,
        initial_ask: NegotiationOffer,
        negotiator: Negotiator,
        owner_id: str = "",
        reservation_id: str | None = None,
    ) -> tuple[DispatchOrder, NegotiationOutcome]:
        """Negotiate terms with a pre-selected vendor, dispatch on accept.

        Responsibility split: the caller selects the vendor (typically
        via :meth:`find_vendors`) and constructs a :class:`Negotiator`
        bound to that vendor's text channel.  This method runs the
        negotiation, then creates a :class:`DispatchOrder` whose status
        reflects the outcome:

        * ``accepted=True``  → ``status='dispatched'``; the final
          offer's ``price`` and ``time`` populate ``cost_estimate``
          and ``scheduled_for`` — the engine commits to what was
          agreed, not the vendor's default hourly rate.
        * ``accepted=False`` → ``status='negotiation_failed'`` and
          the terminal reason is preserved in ``notes`` for audit.
          No work is scheduled.

        Per-round ACCEPT / REJECT DecisionCases are already emitted by
        :class:`Negotiator._log_round`; this method deliberately does
        not also call :meth:`OpsDecisionLogger.log_vendor_dispatch` to
        avoid double-counting the same event in the learning signal.

        Args:
            vendor: The vendor the caller has selected.
            property_id: Property the work is for.
            task_description: Human-readable job summary, stored on
                the resulting order.
            target: Engine constraints driving ACCEPT / COUNTER /
                REJECT decisions inside the negotiator.
            initial_ask: Opening offer the negotiator will send first.
            negotiator: Pre-configured orchestrator bound to the
                vendor's channel.
            owner_id: Property owner — forwarded to the negotiator so
                its DecisionCases have the correct scope.
            reservation_id: Optional reservation this dispatch is
                attached to.

        Returns:
            Tuple ``(order, outcome)``.  The order is always recorded
            in :attr:`_orders` so dashboards see the attempt even on
            the negotiation-failure path.
        """
        outcome = await negotiator.negotiate(
            initial_ask=initial_ask,
            target=target,
            property_id=property_id,
            owner_id=owner_id,
            vendor_name=vendor.name,
            reservation_id=reservation_id,
        )

        if not outcome.accepted:
            order = DispatchOrder(
                vendor_id=vendor.vendor_id,
                property_id=property_id,
                task_description=task_description,
                status="negotiation_failed",
                notes=outcome.reason,
            )
            self._orders[order.order_id] = order
            logger.info(
                "Negotiation with %s failed (%s); no dispatch created",
                vendor.name, outcome.reason,
            )
            return order, outcome

        final = outcome.final_offer
        agreed_price = (
            final.price
            if final is not None and final.price is not None
            else vendor.hourly_rate * 2
        )
        agreed_time = final.time if final is not None else ""

        order = DispatchOrder(
            vendor_id=vendor.vendor_id,
            property_id=property_id,
            task_description=task_description,
            status="dispatched",
            scheduled_for=agreed_time,
            cost_estimate=agreed_price,
            notes=final.notes if final is not None else "",
        )
        self._orders[order.order_id] = order
        logger.info(
            "Negotiated dispatch to %s (order=%s, price=%.2f, time=%s)",
            vendor.name, order.order_id, order.cost_estimate,
            order.scheduled_for or "unscheduled",
        )
        return order, outcome

    async def _emit_dispatch_case(
        self,
        *,
        property_id: str,
        owner_id: str,
        reservation_id: str | None,
        vendor_name: str,
        resolved: bool,
        details: dict[str, Any],
    ) -> None:
        """Forward a dispatch outcome to the ops logger when configured.

        Kept private so the dispatch method stays readable; gates on
        ``owner_id`` because scope-based pattern learning is keyed on
        owner identity and would be misleading without it.
        """
        if self._ops_logger is None or not owner_id:
            return
        await self._ops_logger.log_vendor_dispatch(
            property_id=property_id,
            owner_id=owner_id,
            reservation_id=reservation_id,
            vendor_name=vendor_name,
            resolved=resolved,
            details=details,
        )

    def update_order_status(
        self, order_id: str, status: str, notes: str = ""
    ) -> DispatchOrder | None:
        order = self._orders.get(order_id)
        if not order:
            return None
        order.status = status
        if notes:
            order.notes = notes
        if status == "completed":
            order.completed_at = datetime.now(timezone.utc).isoformat()
        logger.info("Order %s status → %s", order_id, status)
        return order

    def get_order(self, order_id: str) -> DispatchOrder | None:
        return self._orders.get(order_id)

    def list_orders(self, property_id: str | None = None) -> list[DispatchOrder]:
        orders = list(self._orders.values())
        if property_id:
            orders = [o for o in orders if o.property_id == property_id]
        return orders


class PaymentTracker:
    """Tracks payments to cleaners and vendors."""

    def __init__(self) -> None:
        self._payments: dict[str, PaymentRecord] = {}

    def create_payment(
        self,
        recipient_name: str,
        recipient_type: str,
        amount: float,
        currency: str = "USD",
        description: str = "",
    ) -> PaymentRecord:
        payment = PaymentRecord(
            recipient_name=recipient_name,
            recipient_type=recipient_type,
            amount=amount,
            currency=currency,
            description=description,
        )
        self._payments[payment.payment_id] = payment
        logger.info(
            "Payment created: %s → %s %.2f %s",
            payment.payment_id, recipient_name, amount, currency,
        )
        return payment

    def approve_payment(self, payment_id: str) -> PaymentRecord | None:
        p = self._payments.get(payment_id)
        if p:
            p.status = "approved"
            logger.info("Payment %s approved", payment_id)
        return p

    def mark_paid(self, payment_id: str) -> PaymentRecord | None:
        p = self._payments.get(payment_id)
        if p:
            p.status = "paid"
            p.paid_at = datetime.now(timezone.utc).isoformat()
            logger.info("Payment %s marked as paid", payment_id)
        return p

    def get_payment(self, payment_id: str) -> PaymentRecord | None:
        return self._payments.get(payment_id)

    def list_payments(self, status: str | None = None) -> list[PaymentRecord]:
        payments = list(self._payments.values())
        if status:
            payments = [p for p in payments if p.status == status]
        return payments

    def get_total_pending(self) -> float:
        return sum(p.amount for p in self._payments.values() if p.status in ("pending", "approved"))

    def get_total_paid(self) -> float:
        return sum(p.amount for p in self._payments.values() if p.status == "paid")
