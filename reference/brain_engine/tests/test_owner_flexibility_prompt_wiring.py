"""Tests for the owner-flexibility → property-knowledge wiring (R2).

``PropertyProfile.static_payload`` is a flat snapshot of the
property's catalogue data (WiFi, parking, amenities, descriptions).
Owner-level *carve-outs* — "baby crib available for reservations
over $2000 at $50", "extra guest fee $30/night", "early check-in
paid" — live in a separate ``owner_flexibility_profiles`` JSONB
table consumed by the ExecutionOrchestrator.  Pre-R2 the
conversation pipeline read ``static_payload`` only, so the agent
never saw the owner overrides; this produced the baby-crib denial
captured on 2026-05-18 (Sandbox UI screenshots 4.png + 5.png).

This module pins the new wiring:

* ``ConversationService.__init__`` accepts an
  ``owner_profile_store`` keyword argument (``None`` keeps legacy
  behaviour byte-identical).
* :meth:`ConversationService._load_owner_flexibility_block` is a
  resilient async helper: missing store / missing owner / store
  failure / no snapshot all collapse to ``""``.
* :func:`_format_owner_flexibility` renders the snapshot's
  five field groups — capacity, fee_rules, stay_rules,
  checkin_rules, amenity_exceptions — as a Markdown block the
  LLM treats as authoritative overrides.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from brain_engine.conversation.models import (
    ConversationRequest,
    PipelineState,
)
from brain_engine.conversation.service import (
    ConversationService,
    _format_owner_flexibility,
)
from brain_engine.owner_profile.models import (
    AmenityException,
    CheckInRules,
    FeeRules,
    OccupancyCapacity,
    OwnerFlexibilityProfile,
    StayRules,
)

# -- _format_owner_flexibility: renderer contract -----------------------


def _profile(**overrides: Any) -> OwnerFlexibilityProfile:
    """Build an ``OwnerFlexibilityProfile`` with sensible defaults."""
    base = {
        "owner_id": "O1",
        "property_id": "P1",
    }
    base.update(overrides)
    return OwnerFlexibilityProfile(**base)


def test_format_owner_flexibility_empty_profile_returns_empty_string() -> None:
    """A snapshot with no overrides must produce an empty block so
    the caller can splice without a dangling header — the most
    common case before owners enter their first override."""
    rendered = _format_owner_flexibility(_profile())
    assert rendered == ""


def test_format_owner_flexibility_renders_baby_crib_carve_out() -> None:
    """The 2026-05-18 baby-crib regression: an AmenityException
    pinned ``available=True`` with notes describing the conditional
    must surface in the rendered block so the agent quotes the
    rule instead of denying."""
    profile = _profile(
        amenity_exceptions=(
            AmenityException(
                amenity_code="baby_crib",
                available=True,
                notes="Available for reservations over $2000, $50 fee",
            ),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "Owner Amenity Exceptions" in rendered
    assert "baby_crib" in rendered
    assert "AVAILABLE" in rendered
    assert "$2000" in rendered
    assert "$50 fee" in rendered


def test_format_owner_flexibility_renders_denied_amenity() -> None:
    """``available=False`` must surface as ``NOT AVAILABLE`` so the
    LLM never tries to upsell an explicitly denied amenity."""
    profile = _profile(
        amenity_exceptions=(
            AmenityException(
                amenity_code="extra_bed",
                available=False,
                notes="Permanently removed",
            ),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "extra_bed: NOT AVAILABLE — Permanently removed" in rendered


def test_format_owner_flexibility_renders_checkin_rules() -> None:
    """Closes the late-check-in cross-property leak (Bug G context):
    when the owner has stated a late-checkout policy the block must
    quote it verbatim so the LLM does not infer paid-pricing from
    chunks belonging to a different property."""
    profile = _profile(
        checkin_rules=CheckInRules(
            std_checkin_time="15:00",
            std_checkout_time="11:00",
            early_checkin_policy="ok within 1h",
            late_checkout_policy="free until 13:00",
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "Owner Check-in Rules" in rendered
    assert "Standard check-in time: 15:00" in rendered
    assert "Standard check-out time: 11:00" in rendered
    assert "Early check-in policy: ok within 1h" in rendered
    assert "Late check-out policy: free until 13:00" in rendered


def test_format_owner_flexibility_renders_fee_and_stay_rules() -> None:
    """Fees + stay rules surface so the LLM quotes the owner
    baseline rather than improvising surcharges."""
    profile = _profile(
        fee_rules=FeeRules(
            extra_guest_fee=30.0,
            pet_fee=50.0,
            cleaning_fee=120.0,
        ),
        stay_rules=StayRules(
            default_min_stay=2,
            max_stay=14,
            advance_booking_window=365,
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "Owner Fee Rules" in rendered
    assert "Extra guest fee: 30.0" in rendered
    assert "Pet fee: 50.0" in rendered
    assert "Cleaning fee: 120.0" in rendered
    assert "Owner Stay Rules" in rendered
    assert "Default min stay: 2" in rendered
    assert "Max stay: 14" in rendered
    assert "Advance booking window (days): 365" in rendered


def test_format_owner_flexibility_renders_capacity_pet_policy() -> None:
    """``OccupancyCapacity.pets_allowed`` carries a tri-state
    (``True`` / ``False`` / ``None``); only the two booleans must
    surface — ``None`` means "case-by-case" and must NOT emit a
    misleading line."""
    explicit_yes = _profile(
        occupancy_capacity=OccupancyCapacity(pets_allowed=True),
    )
    explicit_no = _profile(
        occupancy_capacity=OccupancyCapacity(pets_allowed=False),
    )
    unstated = _profile(
        occupancy_capacity=OccupancyCapacity(pets_allowed=None),
    )

    assert "Pets allowed: yes" in _format_owner_flexibility(explicit_yes)
    assert "Pets allowed: no" in _format_owner_flexibility(explicit_no)
    assert "Pets allowed" not in _format_owner_flexibility(unstated)


def test_format_owner_flexibility_starts_with_authoritative_header() -> None:
    """The block must lead with a stable header so the LLM treats
    the rules as authoritative overrides (and so downstream tests
    have a stable anchor to assert on)."""
    profile = _profile(
        fee_rules=FeeRules(cleaning_fee=100.0),
    )
    rendered = _format_owner_flexibility(profile)
    assert rendered.startswith("## Owner Flexibility Rules")


# -- _load_owner_flexibility_block: integration with the service --------


def _bare_service(
    *,
    owner_profile_store: Any = None,
    profile_store: Any = None,
) -> ConversationService:
    """Build a minimal ``ConversationService`` skeleton for the helper."""
    svc = ConversationService.__new__(ConversationService)
    svc._owner_profile_store = owner_profile_store
    svc._profile_store = profile_store
    return svc


def _state(property_id: str = "P1", customer_id: str = "C1") -> PipelineState:
    request = ConversationRequest(
        customer_id=customer_id,
        property_id=property_id,
    )
    return PipelineState(request=request)


@pytest.mark.asyncio
async def test_load_owner_block_no_store_returns_empty() -> None:
    """No store injected → no-op even if the property is known.
    Pins the byte-identical-by-default contract."""
    svc = _bare_service(owner_profile_store=None)
    state = _state()

    block = await svc._load_owner_flexibility_block(state, "P1")

    assert block == ""


@pytest.mark.asyncio
async def test_load_owner_block_resolves_customer_id_when_no_owner() -> None:
    """``_resolve_owner_id`` falls back to ``customer_id`` when the
    property profile has no explicit owner.  The owner-block helper
    must accept that fallback and still call the store."""
    store = AsyncMock()
    store.get = AsyncMock(
        return_value=_profile(
            amenity_exceptions=(
                AmenityException(
                    amenity_code="baby_crib",
                    available=True,
                    notes="Yes for >$2000",
                ),
            ),
        ),
    )
    svc = _bare_service(owner_profile_store=store)
    state = _state(customer_id="C1")

    block = await svc._load_owner_flexibility_block(state, "P1")

    store.get.assert_awaited_once_with("C1", "P1")
    assert "baby_crib" in block


@pytest.mark.asyncio
async def test_load_owner_block_swallows_store_exception() -> None:
    """A broken owner store must never break the live chat — the
    helper logs and returns the empty string so the rest of the
    property-knowledge load proceeds."""
    store = AsyncMock()
    store.get = AsyncMock(side_effect=RuntimeError("postgres down"))
    svc = _bare_service(owner_profile_store=store)
    state = _state()

    block = await svc._load_owner_flexibility_block(state, "P1")

    assert block == ""


@pytest.mark.asyncio
async def test_load_owner_block_none_snapshot_returns_empty() -> None:
    """Cache miss (``None``) maps to the empty string, not to an
    Exception."""
    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    svc = _bare_service(owner_profile_store=store)
    state = _state()

    block = await svc._load_owner_flexibility_block(state, "P1")

    assert block == ""


@pytest.mark.asyncio
async def test_load_owner_block_skips_when_owner_id_blank() -> None:
    """When both ``property_id`` and ``customer_id`` are empty the
    resolver returns ``""``; the helper must short-circuit BEFORE
    calling the store so a misconfigured request cannot probe a
    "" key."""
    store = AsyncMock()
    store.get = AsyncMock()
    svc = _bare_service(owner_profile_store=store)
    request = ConversationRequest(customer_id="")
    state = PipelineState(request=request)

    block = await svc._load_owner_flexibility_block(state, "")

    store.get.assert_not_called()
    assert block == ""
