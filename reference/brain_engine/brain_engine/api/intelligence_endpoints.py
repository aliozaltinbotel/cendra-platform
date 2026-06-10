"""Intelligence endpoints — upsell, analytics, and rule creation.

Operational intelligence layer that powers the Cendra dashboard:

- **Upsell evaluation**: Feasibility + pricing for a reservation.
- **Sentiment analytics**: Aggregated guest sentiment over time.
- **Escalation breakdown**: Escalations grouped by category.
- **AI accuracy**: Per-property AI acceptance rate.
- **NL rule creation**: Natural language → CompositeRule.

All endpoints follow Brain Engine conventions:
- APIRouter with ``/api/v1`` prefix.
- Pydantic request/response models.
- Structured logging.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from brain_engine.analytics.service import AnalyticsService
from brain_engine.calendar.evaluator import CalendarEvaluator
from brain_engine.patterns.store import InMemoryDecisionCaseStore
from brain_engine.rules.nl_creator import NaturalLanguageRuleCreator
from brain_engine.upsell.evaluator import UpsellEvaluator

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["frontend"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class UpsellEvaluateRequest(BaseModel):
    """Request to evaluate upsell feasibility for a reservation."""

    property_id: str = Field(description="Property identifier.")
    reservation_id: str = Field(description="Reservation identifier.")
    checkin_date: str = Field(description="ISO check-in date (YYYY-MM-DD).")
    checkout_date: str = Field(description="ISO check-out date (YYYY-MM-DD).")
    calendar_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Calendar availability data.",
    )
    adr: float = Field(default=0.0, description="Average daily rate.")
    standard_checkin_hour: int = Field(default=15, ge=0, le=23)
    standard_checkout_hour: int = Field(default=10, ge=0, le=23)
    property_config: dict[str, Any] | None = Field(
        default=None,
        description="Per-property pricing overrides.",
    )


class UpsellEvaluateResponse(BaseModel):
    """Response with upsell evaluation results."""

    property_id: str
    reservation_id: str
    options: list[dict[str, Any]]
    total_potential_revenue: float
    feasible_count: int
    total_count: int


class SentimentRequest(BaseModel):
    """Request for sentiment analytics."""

    property_id: str | None = Field(
        default=None,
        description="Property filter (None = all properties).",
    )
    days: int = Field(default=90, ge=1, le=365)


class SentimentResponse(BaseModel):
    """Response with aggregated sentiment."""

    score: float
    label: str
    description: str
    total_cases: int
    positive_count: int
    negative_count: int
    neutral_count: int


class EscalationRequest(BaseModel):
    """Request for escalation breakdown."""

    property_id: str | None = Field(
        default=None,
        description="Property filter (None = all properties).",
    )
    days: int = Field(default=90, ge=1, le=365)


class EscalationResponse(BaseModel):
    """Response with escalation breakdown."""

    categories: dict[str, int]
    total_escalated: int
    total_auto_resolved: int
    escalation_rate: float


class AccuracyRequest(BaseModel):
    """Request for AI accuracy per property."""

    property_id: str = Field(description="Property identifier.")
    days: int = Field(default=90, ge=1, le=365)


class AccuracyResponse(BaseModel):
    """Response with AI accuracy metrics."""

    property_id: str
    accuracy_pct: float
    total_decisions: int
    accepted_count: int
    overridden_count: int
    escalated_count: int


class CreateRuleNLRequest(BaseModel):
    """Request to create a rule from natural language."""

    description: str = Field(
        description="Natural language rule description.",
        min_length=5,
        max_length=1000,
    )
    property_id: str = Field(
        default="",
        description="Property scope for the rule.",
    )
    agent_id: str = Field(
        default="",
        description="Agent to link the rule to.",
    )


class CreateRuleNLResponse(BaseModel):
    """Response with created rule details."""

    success: bool
    explanation: str = ""
    rule: dict[str, Any] | None = None
    rule_display: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Dependency injection — singleton services
# ---------------------------------------------------------------------------

_case_store = InMemoryDecisionCaseStore()
_calendar_evaluator = CalendarEvaluator()
_upsell_evaluator = UpsellEvaluator(calendar_evaluator=_calendar_evaluator)
_analytics_service = AnalyticsService(store=_case_store)
_nl_rule_creator = NaturalLanguageRuleCreator()


# ---------------------------------------------------------------------------
# Upsell endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/upsell/evaluate",
    response_model=UpsellEvaluateResponse,
)
async def evaluate_upsell(
    request: UpsellEvaluateRequest,
) -> UpsellEvaluateResponse:
    """Evaluate upsell feasibility for a reservation.

    Returns Early Check-in, Late Check-out, Gap Night, and Late Check-in
    options with pricing and time ranges.
    """
    evaluation = _upsell_evaluator.evaluate(
        property_id=request.property_id,
        reservation_id=request.reservation_id,
        checkin_date=request.checkin_date,
        checkout_date=request.checkout_date,
        calendar_data=request.calendar_data,
        adr=request.adr,
        standard_checkin_hour=request.standard_checkin_hour,
        standard_checkout_hour=request.standard_checkout_hour,
        property_config=request.property_config,
    )

    data = evaluation.to_dict()
    return UpsellEvaluateResponse(**data)


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------

@router.post("/analytics/sentiment", response_model=SentimentResponse)
async def get_sentiment(request: SentimentRequest) -> SentimentResponse:
    """Get aggregated guest sentiment for the dashboard.

    Returns sentiment score (1-10), label, and breakdown of positive,
    negative, and neutral cases over the specified time window.
    """
    result = await _analytics_service.compute_sentiment(
        property_id=request.property_id,
        days=request.days,
    )
    return SentimentResponse(**result.to_dict())


@router.post(
    "/analytics/escalations",
    response_model=EscalationResponse,
)
async def get_escalations(
    request: EscalationRequest,
) -> EscalationResponse:
    """Get escalation breakdown by category for the dashboard.

    Returns counts grouped by dashboard categories (Availability,
    Booking Modification, Complaint, Discount, etc.).
    """
    result = await _analytics_service.compute_escalation_breakdown(
        property_id=request.property_id,
        days=request.days,
    )
    return EscalationResponse(**result.to_dict())


@router.post("/analytics/accuracy", response_model=AccuracyResponse)
async def get_accuracy(request: AccuracyRequest) -> AccuracyResponse:
    """Get AI accuracy percentage for a property.

    Returns the percentage of AI decisions accepted without PM override,
    matching the 'AI Accuracy' column in Knowledge Base property list.
    """
    result = await _analytics_service.compute_accuracy(
        property_id=request.property_id,
        days=request.days,
    )
    return AccuracyResponse(**result.to_dict())


# ---------------------------------------------------------------------------
# Rule creator endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/rules/create-from-nl",
    response_model=CreateRuleNLResponse,
)
async def create_rule_from_nl(
    request: CreateRuleNLRequest,
) -> CreateRuleNLResponse:
    """Create a CompositeRule from natural language description.

    The PM describes the rule in plain English (e.g. "Auto-label
    high-value bookings over $1000"). The LLM parses it into a
    structured CompositeRule with condition and behaviors.

    The rule is returned for PM review — not activated automatically.
    """
    result = await _nl_rule_creator.create_rule(
        description=request.description,
        property_id=request.property_id,
        agent_id=request.agent_id,
    )

    data = result.to_dict()
    return CreateRuleNLResponse(
        success=data["success"],
        explanation=data.get("explanation", ""),
        rule=data.get("rule"),
        rule_display=data.get("rule_display", ""),
        error=data.get("error", ""),
    )
