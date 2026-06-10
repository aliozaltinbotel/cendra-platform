"""Tests for owner-curated local recommendations (R5).

The Sandbox UI feedback captured on 2026-05-18 (screenshot 8.png)
showed the agent answering "I currently don't have specific local
recommendations for the area" + "I currently don't have
information about the nearest restaurant" — two consecutive
deflections to PM for questions that should have been answered
from owner-vetted data.

Pre-R5 the schema had nowhere to store such data: every existing
field group (occupancy, fees, stay, checkin, amenities,
flexibility, approval) targets booking economics, not nearby
places.  This module pins the new storage:

* :class:`LocalRecommendation` — frozen value object the owner /
  PM can curate per property.
* :class:`OwnerFlexibilityProfile.local_recommendations` — a tuple
  field defaulting to ``()`` so existing profiles keep
  byte-identical semantics.
* :data:`FIELD_GROUPS` carries ``"local_recommendations"`` so
  source-of-truth bookkeeping stays uniform with the other groups.
* The conversation pipeline's ``_format_owner_flexibility`` block
  surfaces the entries grouped by category — the LLM scans
  ``**restaurant**`` / ``**cafe**`` sub-headers instead of
  re-parsing a flat list.
"""

from __future__ import annotations

from brain_engine.conversation.service import _format_owner_flexibility
from brain_engine.owner_profile.models import (
    FIELD_GROUPS,
    LocalRecommendation,
    OwnerFlexibilityProfile,
)

# -- model contract ------------------------------------------------------


def test_local_recommendation_minimal_fields() -> None:
    """``category`` + ``name`` are the only required positional
    arguments; ``distance`` and ``notes`` default to empty strings
    so callers can carry the bare minimum without keyword spam."""
    rec = LocalRecommendation(category="restaurant", name="La Casa")
    assert rec.category == "restaurant"
    assert rec.name == "La Casa"
    assert rec.distance == ""
    assert rec.notes == ""


def test_owner_profile_local_recommendations_defaults_empty() -> None:
    """Existing profiles persisted before R5 must continue to
    construct cleanly — the new field defaults to ``()`` so the
    pre-R5 byte-shape is preserved."""
    profile = OwnerFlexibilityProfile(owner_id="O1", property_id="P1")
    assert profile.local_recommendations == ()


def test_field_groups_includes_local_recommendations() -> None:
    """``FIELD_GROUPS`` is the contract every source-of-truth key
    must match — the new group has to be listed so harvester /
    PM-correction writes can tag provenance for it."""
    assert "local_recommendations" in FIELD_GROUPS


# -- renderer integration ------------------------------------------------


def test_format_owner_flexibility_skips_block_when_no_recommendations() -> None:
    """A profile with no local recommendations must not render an
    empty header — the caller can splice cleanly without a stray
    ``### Owner Local Recommendations`` line."""
    profile = OwnerFlexibilityProfile(owner_id="O1", property_id="P1")
    rendered = _format_owner_flexibility(profile)
    assert "Local Recommendations" not in rendered


def test_format_owner_flexibility_renders_single_recommendation() -> None:
    """One entry must surface with its name, distance and notes —
    this is the regression guard for the 8.png "nearest restaurant"
    scenario."""
    profile = OwnerFlexibilityProfile(
        owner_id="O1",
        property_id="P1",
        local_recommendations=(
            LocalRecommendation(
                category="restaurant",
                name="La Casa",
                distance="500m",
                notes="Italian, open until midnight",
            ),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "Owner Local Recommendations" in rendered
    assert "**restaurant**:" in rendered
    assert "La Casa" in rendered
    assert "(500m)" in rendered
    assert "Italian, open until midnight" in rendered


def test_format_owner_flexibility_groups_by_category() -> None:
    """Multiple categories must render as separate sub-headers in
    sorted order so the LLM scans deterministically."""
    profile = OwnerFlexibilityProfile(
        owner_id="O1",
        property_id="P1",
        local_recommendations=(
            LocalRecommendation(category="restaurant", name="La Casa"),
            LocalRecommendation(category="cafe", name="Brew"),
            LocalRecommendation(category="restaurant", name="El Toro"),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    cafe_idx = rendered.index("**cafe**:")
    restaurant_idx = rendered.index("**restaurant**:")
    assert cafe_idx < restaurant_idx  # sorted alphabetically
    assert "La Casa" in rendered
    assert "El Toro" in rendered
    assert "Brew" in rendered


def test_format_owner_flexibility_handles_missing_distance_and_notes() -> None:
    """Entries with no distance / notes must render cleanly — the
    pipe characters and dashes only appear when their content does."""
    profile = OwnerFlexibilityProfile(
        owner_id="O1",
        property_id="P1",
        local_recommendations=(
            LocalRecommendation(category="pharmacy", name="Apoteca 24h"),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "- Apoteca 24h\n" in rendered + "\n"
    assert "(" not in rendered.split("Apoteca 24h")[1].split("\n")[0]


def test_format_owner_flexibility_uses_general_for_empty_category() -> None:
    """An entry without a category must not vanish — it falls into
    the ``general`` bucket so the LLM still surfaces the name."""
    profile = OwnerFlexibilityProfile(
        owner_id="O1",
        property_id="P1",
        local_recommendations=(
            LocalRecommendation(category="", name="Mystery Spot"),
        ),
    )
    rendered = _format_owner_flexibility(profile)

    assert "**general**:" in rendered
    assert "Mystery Spot" in rendered


# -- postgres serialisation round-trip -----------------------------------


def test_local_rec_dict_round_trip() -> None:
    """The postgres store persists JSONB by calling
    ``_local_rec_to_dict`` on every entry and rebuilds the tuple
    with ``_local_rec_from_dict``.  Pin the round-trip so a future
    refactor cannot silently drop fields."""
    from brain_engine.owner_profile.postgres_store import (
        _local_rec_from_dict,
        _local_rec_to_dict,
    )

    original = LocalRecommendation(
        category="transport",
        name="Metro station Alpha",
        distance="3 stops",
        notes="Line 2, opens 06:00",
    )
    payload = _local_rec_to_dict(original)
    restored = _local_rec_from_dict(payload)

    assert restored == original


def test_local_rec_from_dict_tolerates_missing_keys() -> None:
    """A row written before the migration may carry partial JSON
    (only ``category`` + ``name``).  The decoder must fill empty
    strings for the missing fields rather than raise — pinned
    explicitly so a defensive default cannot drift away."""
    from brain_engine.owner_profile.postgres_store import (
        _local_rec_from_dict,
    )

    rec = _local_rec_from_dict({"category": "cafe", "name": "Brew"})

    assert rec == LocalRecommendation(
        category="cafe",
        name="Brew",
        distance="",
        notes="",
    )
