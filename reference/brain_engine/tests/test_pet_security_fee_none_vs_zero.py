"""Pet-fee and security-deposit ``None`` vs ``0`` disambiguation.

Same defect class as ``test_cleaning_fee_none_vs_zero`` (closed for
``cleaningFee`` in PR #377), now covering the two remaining fee fields.

Before this fix the unified-data reader coerced a missing/``null``
``petFee`` / ``securityDepositFee`` to ``0.0`` (via :func:`_float`), and
the prompt renderer used truthy checks (``if sp.get("pet_fee"):`` /
``if sp.get("security_deposit_fee"):``).  Together they collapsed three
distinct states into one silent outcome:

* fee genuinely **unknown** (source ``null``)  → dropped (correct),
* fee genuinely **zero** (owner charges nothing) → dropped (WRONG —
  the LLM has no signal and may hallucinate a non-zero fee),
* fee **positive** → rendered.

The read layer now preserves ``None`` (``_optional_float``) so the
renderer can tell "unknown" from "zero" and emit an explicit "no fee"
line for a true zero.
"""

from __future__ import annotations

from typing import Any

import pytest

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

# (source camelCase key, parsed attribute, static_payload key, label).
_FEES = [
    ("petFee", "pet_fee", "pet_fee", "Pet fee"),
    (
        "securityDepositFee",
        "security_deposit_fee",
        "security_deposit_fee",
        "Security deposit",
    ),
]

# Explicit "no fee" sentence each field emits for a genuine zero.
_ZERO_LINE = {
    "pet_fee": "Pet fee: none (no separate pet fee charged)",
    "security_deposit_fee": "Security deposit: none (no deposit required)",
}


# -- read layer: null is preserved, zero stays zero ---------------------


def _detail_fee(source_key: str, attr: str, raw: Any) -> float | None:
    """Parse a detail row whose ``data`` carries ``raw`` under
    ``source_key`` and return the mapped ``attr`` (``raw is _MISSING``
    ⇒ field absent)."""
    data: dict[str, Any] = {} if raw is _MISSING else {source_key: raw}
    return getattr(_parse_property_detail({"data": data}), attr)


@pytest.mark.parametrize("source_key, attr", [(f[0], f[1]) for f in _FEES])
def test_null_fee_maps_to_none(source_key: str, attr: str) -> None:
    """Source ``null`` must survive as ``None`` (unknown), not 0.0."""
    assert _detail_fee(source_key, attr, None) is None


@pytest.mark.parametrize("source_key, attr", [(f[0], f[1]) for f in _FEES])
def test_missing_fee_maps_to_none(source_key: str, attr: str) -> None:
    """A field absent from the payload is unknown, not zero."""
    assert _detail_fee(source_key, attr, _MISSING) is None


@pytest.mark.parametrize("source_key, attr", [(f[0], f[1]) for f in _FEES])
def test_zero_fee_maps_to_zero(source_key: str, attr: str) -> None:
    """A genuine zero stays a float ``0.0`` — distinct from unknown."""
    assert _detail_fee(source_key, attr, 0) == 0.0


@pytest.mark.parametrize("source_key, attr", [(f[0], f[1]) for f in _FEES])
def test_positive_fee_maps_to_value(source_key: str, attr: str) -> None:
    """A positive fee is coerced to its float value."""
    assert _detail_fee(source_key, attr, 25) == 25.0


# -- renderer: three-way rendering of the fee line ----------------------


def _profile(payload_key: str, value: Any = _MISSING) -> PropertyProfile:
    """Minimal :class:`PropertyProfile` whose ``static_payload``
    carries (or omits) ``payload_key``."""
    payload: dict[str, Any] = {}
    if value is not _MISSING:
        payload[payload_key] = value
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


@pytest.mark.parametrize(
    "payload_key, label", [(f[2], f[3]) for f in _FEES],
)
def test_unknown_fee_emits_no_line(payload_key: str, label: str) -> None:
    """``None`` (unknown) must not produce any fee line."""
    rendered = _format_profile_knowledge(_profile(payload_key, None))
    assert label not in rendered


@pytest.mark.parametrize(
    "payload_key, label", [(f[2], f[3]) for f in _FEES],
)
def test_absent_fee_emits_no_line(payload_key: str, label: str) -> None:
    """A payload without the key behaves like unknown."""
    rendered = _format_profile_knowledge(_profile(payload_key))
    assert label not in rendered


@pytest.mark.parametrize("payload_key", [f[2] for f in _FEES])
def test_zero_fee_emits_explicit_no_fee_line(payload_key: str) -> None:
    """A genuine zero must surface an explicit "no fee" statement so
    the LLM cannot invent a charge."""
    rendered = _format_profile_knowledge(_profile(payload_key, 0.0))
    assert _ZERO_LINE[payload_key] in rendered


@pytest.mark.parametrize(
    "payload_key, label", [(f[2], f[3]) for f in _FEES],
)
def test_positive_fee_emits_value_line(payload_key: str, label: str) -> None:
    """A positive fee is rendered verbatim."""
    rendered = _format_profile_knowledge(_profile(payload_key, 25.0))
    assert f"{label}: 25.0" in rendered
