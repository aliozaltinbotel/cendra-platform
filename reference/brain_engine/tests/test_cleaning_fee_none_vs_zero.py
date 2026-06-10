"""Cleaning-fee ``None`` vs ``0`` disambiguation in property knowledge.

Before this fix the unified-data reader coerced a missing/``null``
``cleaningFee`` to ``0.0`` (via :func:`_float`), and the prompt
renderer used a truthy check (``if sp.get("cleaning_fee"):``).  The
two collapsed three distinct states into one silent outcome:

* fee genuinely **unknown** (source ``null``)  → dropped (correct),
* fee genuinely **zero** (owner charges nothing) → dropped (WRONG —
  the LLM has no signal and may hallucinate a non-zero fee),
* fee **positive** → rendered.

The read layer now preserves ``None`` (``_optional_float``) so the
renderer can tell "unknown" from "zero" and emit an explicit
"no separate cleaning fee charged" line for a true zero.
"""

from __future__ import annotations

from typing import Any

from brain_engine.conversation.service import _format_profile_knowledge
from brain_engine.integrations.unified_data.readers import (
    _parse_property_detail,
)
from brain_engine.profiles.models import (
    KnowledgeSection,
    PropertyProfile,
    ReviewAggregate,
)

# A sentinel distinct from ``None``: lets a test say "the key is
# absent from the payload" as opposed to "the key is present as null".
_MISSING: Any = object()


# -- read layer: null is preserved, zero stays zero ---------------------


def _detail_cleaning_fee(raw: Any) -> float | None:
    """Parse a detail row whose ``data`` carries ``raw`` and return
    the mapped ``cleaning_fee`` (``raw is _MISSING`` ⇒ field absent)."""
    data: dict[str, Any] = {} if raw is _MISSING else {"cleaningFee": raw}
    return _parse_property_detail({"data": data}).cleaning_fee


def test_null_cleaning_fee_maps_to_none() -> None:
    """Source ``null`` must survive as ``None`` (unknown), not 0.0."""
    assert _detail_cleaning_fee(None) is None


def test_missing_cleaning_fee_maps_to_none() -> None:
    """A field absent from the payload is unknown, not zero."""
    assert _detail_cleaning_fee(_MISSING) is None


def test_zero_cleaning_fee_maps_to_zero() -> None:
    """A genuine zero stays a float ``0.0`` — distinct from unknown."""
    assert _detail_cleaning_fee(0) == 0.0


def test_positive_cleaning_fee_maps_to_value() -> None:
    """A positive fee is coerced to its float value."""
    assert _detail_cleaning_fee(50) == 50.0


# -- renderer: three-way rendering of the fee line ----------------------


def _profile(cleaning_fee: float | None | object = _MISSING) -> PropertyProfile:
    """Minimal :class:`PropertyProfile` whose ``static_payload``
    carries (or omits) ``cleaning_fee``."""
    payload: dict[str, Any] = {}
    if cleaning_fee is not _MISSING:
        payload["cleaning_fee"] = cleaning_fee
    empty_section = KnowledgeSection(
        name="x", item_count=0, last_ingested_at=None,
    )
    return PropertyProfile(
        property_channel_id="P1",
        pms_id="pms",
        customer_id="c",
        org_id="o",
        provider_type="prov",
        title="T",
        is_active=True,
        city="City",
        country="Country",
        property_type="apartment",
        max_occupancy=4,
        bedrooms=1,
        bathrooms=1.0,
        base_currency="EUR",
        base_price=80.0,
        knowledge_percentage=0.0,
        amenity_codes=(),
        image_count=0,
        room_count=0,
        description_languages=(),
        reservations=empty_section,
        conversations=empty_section,
        rate_plans=empty_section,
        reviews=empty_section,
        review_aggregate=ReviewAggregate(
            total=0, with_rating=0, average_rating=None, latest_review_at=None,
        ),
        static_payload=payload,
    )


def test_unknown_cleaning_fee_emits_no_line() -> None:
    """``None`` (unknown) must not produce any cleaning-fee line."""
    rendered = _format_profile_knowledge(_profile(None))
    assert "Cleaning fee" not in rendered


def test_absent_cleaning_fee_emits_no_line() -> None:
    """A payload without the key behaves like unknown."""
    rendered = _format_profile_knowledge(_profile())
    assert "Cleaning fee" not in rendered


def test_zero_cleaning_fee_emits_explicit_no_fee_line() -> None:
    """A genuine zero must surface an explicit "no fee" statement so
    the LLM cannot invent a charge."""
    rendered = _format_profile_knowledge(_profile(0.0))
    assert "Cleaning fee: none (no separate cleaning fee charged)" in rendered


def test_positive_cleaning_fee_emits_value_line() -> None:
    """A positive fee is rendered verbatim."""
    rendered = _format_profile_knowledge(_profile(50.0))
    assert "Cleaning fee: 50.0" in rendered
