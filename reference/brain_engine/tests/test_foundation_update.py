"""Tests for the foundation update feedback loop (FL-13).

Pins:

* :class:`FoundationUpdateCandidate` constructor invariants.
* :class:`UpdateSeverity` triage thresholds (3 -> LOW, 5 -> MEDIUM,
  10 -> HIGH).
* :class:`InMemoryFoundationUpdateStore` idempotency on the natural
  key ``(foundation_scenario_id, scope, scope_id)``.
* :func:`detect_foundation_drift` aggregation:
    - groups by ``(foundation_scenario_id, scope_id)``,
    - skips cases without ``foundation_scenario_id``,
    - skips cases without ``outcome.human_overrode``,
    - respects the threshold parameter,
    - severity ladder.
* The detector is deterministic — same input ⇒ same candidate ids
  (when ``candidate_id`` is overridden) and same ordering.
"""

from __future__ import annotations

import pytest

from brain_engine.patterns.foundation_update import (
    DEFAULT_DRIFT_THRESHOLD,
    FoundationUpdateCandidate,
    FoundationUpdateStore,
    InMemoryFoundationUpdateStore,
    UpdateSeverity,
    _severity_for,
    detect_foundation_drift,
    upsert_candidates,
)
from brain_engine.patterns.models import (
    BookingStage,
    CaseOutcome,
    DecisionAction,
    DecisionCase,
    DecisionType,
    ResolutionType,
    Scenario,
)

# ── fixtures ──────────────────────────────────────────────── #


def _make_case(
    *,
    foundation_scenario_id: str,
    property_id: str = "prop-1",
    owner_id: str = "owner-1",
    human_overrode: bool = True,
    resolution: ResolutionType | None = ResolutionType.PM_DENIED,
) -> DecisionCase:
    """Return a minimal :class:`DecisionCase` for detector inputs."""
    return DecisionCase(
        stage=BookingStage.IN_STAY,
        scenario=Scenario.EARLY_CHECKIN,
        property_id=property_id,
        owner_id=owner_id,
        decision=DecisionAction(
            action_type=DecisionType.APPROVE,
            params={},
        ),
        outcome=CaseOutcome(
            human_overrode=human_overrode,
            resolution_type=resolution,
        ),
        foundation_scenario_id=foundation_scenario_id,
    )


# ── FoundationUpdateCandidate invariants ──────────────────── #


def test_candidate_requires_foundation_scenario_id() -> None:
    """Empty ``foundation_scenario_id`` raises."""
    with pytest.raises(ValueError, match="foundation_scenario_id"):
        FoundationUpdateCandidate(
            foundation_scenario_id="",
            scope="property",
            scope_id="prop-1",
            override_count=3,
            severity=UpdateSeverity.LOW,
            deviation_evidence="evidence",
        )


def test_candidate_requires_positive_override_count() -> None:
    """``override_count`` must be > 0."""
    with pytest.raises(ValueError, match="override_count"):
        FoundationUpdateCandidate(
            foundation_scenario_id="s1_1_x",
            scope="property",
            scope_id="prop-1",
            override_count=0,
            severity=UpdateSeverity.LOW,
            deviation_evidence="evidence",
        )


def test_candidate_auto_generates_id_and_timestamp() -> None:
    """``candidate_id`` and ``created_at`` are filled when omitted."""
    candidate = FoundationUpdateCandidate(
        foundation_scenario_id="s1_1_x",
        scope="property",
        scope_id="prop-1",
        override_count=3,
        severity=UpdateSeverity.LOW,
        deviation_evidence="evidence",
    )
    assert candidate.candidate_id  # non-empty hex
    assert candidate.created_at is not None


# ── severity ladder ───────────────────────────────────────── #


@pytest.mark.parametrize(
    ("override_count", "expected"),
    [
        (3, UpdateSeverity.LOW),
        (4, UpdateSeverity.LOW),
        (5, UpdateSeverity.MEDIUM),
        (9, UpdateSeverity.MEDIUM),
        (10, UpdateSeverity.HIGH),
        (50, UpdateSeverity.HIGH),
    ],
)
def test_severity_ladder(
    override_count: int,
    expected: UpdateSeverity,
) -> None:
    """The severity ladder triages override counts deterministically."""
    assert _severity_for(override_count) == expected


# ── InMemoryFoundationUpdateStore ─────────────────────────── #


def test_in_memory_store_satisfies_protocol() -> None:
    """The in-memory store satisfies the runtime Protocol."""
    store = InMemoryFoundationUpdateStore()
    assert isinstance(store, FoundationUpdateStore)


@pytest.mark.asyncio
async def test_in_memory_store_upserts_and_reads() -> None:
    """A round-trip upsert + ``get`` returns the same value object."""
    store = InMemoryFoundationUpdateStore()
    candidate = FoundationUpdateCandidate(
        foundation_scenario_id="s1_1_x",
        scope="property",
        scope_id="prop-1",
        override_count=3,
        severity=UpdateSeverity.LOW,
        deviation_evidence="evidence",
        source_case_ids=("case-1", "case-2"),
    )
    await store.upsert(candidate)
    fetched = await store.get(candidate.candidate_id)
    assert fetched == candidate


@pytest.mark.asyncio
async def test_in_memory_store_idempotent_on_natural_key() -> None:
    """Re-upserting the same natural key refreshes in place."""
    store = InMemoryFoundationUpdateStore()
    first = FoundationUpdateCandidate(
        foundation_scenario_id="s1_1_x",
        scope="property",
        scope_id="prop-1",
        override_count=3,
        severity=UpdateSeverity.LOW,
        deviation_evidence="initial evidence",
    )
    await store.upsert(first)
    second = FoundationUpdateCandidate(
        foundation_scenario_id="s1_1_x",
        scope="property",
        scope_id="prop-1",
        override_count=7,
        severity=UpdateSeverity.MEDIUM,
        deviation_evidence="updated evidence",
    )
    await store.upsert(second)
    pending = await store.list_pending()
    assert len(pending) == 1
    assert pending[0].override_count == 7
    assert pending[0].severity == UpdateSeverity.MEDIUM
    # The previous candidate id is no longer addressable.
    assert await store.get(first.candidate_id) is None


@pytest.mark.asyncio
async def test_in_memory_store_filters_by_scope() -> None:
    """``list_pending`` filters by ``scope`` and ``scope_id``."""
    store = InMemoryFoundationUpdateStore()
    candidates = [
        FoundationUpdateCandidate(
            foundation_scenario_id="s1_1_x",
            scope="property",
            scope_id="prop-1",
            override_count=3,
            severity=UpdateSeverity.LOW,
            deviation_evidence="ev",
        ),
        FoundationUpdateCandidate(
            foundation_scenario_id="s1_2_y",
            scope="property",
            scope_id="prop-2",
            override_count=3,
            severity=UpdateSeverity.LOW,
            deviation_evidence="ev",
        ),
        FoundationUpdateCandidate(
            foundation_scenario_id="s1_3_z",
            scope="owner",
            scope_id="owner-9",
            override_count=3,
            severity=UpdateSeverity.LOW,
            deviation_evidence="ev",
        ),
    ]
    for c in candidates:
        await store.upsert(c)
    prop_rows = await store.list_pending(scope="property")
    assert {row.scope_id for row in prop_rows} == {"prop-1", "prop-2"}
    prop1_rows = await store.list_pending(
        scope="property",
        scope_id="prop-1",
    )
    assert len(prop1_rows) == 1
    assert prop1_rows[0].foundation_scenario_id == "s1_1_x"


# ── detect_foundation_drift ───────────────────────────────── #


def test_detect_empty_iterable_returns_empty_tuple() -> None:
    """No cases ⇒ no candidates."""
    assert detect_foundation_drift([]) == ()


def test_detect_rejects_non_positive_threshold() -> None:
    """``threshold`` must be positive."""
    with pytest.raises(ValueError, match="threshold"):
        detect_foundation_drift([], threshold=0)


def test_detect_skips_cases_without_foundation_scenario_id() -> None:
    """Legacy / unlinked cases are silently dropped."""
    cases = [
        _make_case(foundation_scenario_id=""),
        _make_case(foundation_scenario_id=""),
        _make_case(foundation_scenario_id=""),
    ]
    assert detect_foundation_drift(cases) == ()


def test_detect_skips_cases_without_human_override() -> None:
    """Only ``outcome.human_overrode=True`` cases count toward drift."""
    cases = [
        _make_case(
            foundation_scenario_id="s1_1_x",
            human_overrode=False,
        )
        for _ in range(5)
    ]
    assert detect_foundation_drift(cases) == ()


def test_detect_below_threshold_emits_nothing() -> None:
    """Two overrides do not surface a candidate at the default threshold."""
    cases = [
        _make_case(foundation_scenario_id="s1_1_x") for _ in range(2)
    ]
    assert detect_foundation_drift(cases) == ()


def test_detect_at_threshold_emits_low_severity() -> None:
    """Three overrides cross the default threshold (LOW)."""
    cases = [
        _make_case(foundation_scenario_id="s1_1_x") for _ in range(3)
    ]
    candidates = detect_foundation_drift(cases)
    assert len(candidates) == 1
    assert candidates[0].override_count == 3
    assert candidates[0].severity == UpdateSeverity.LOW
    assert candidates[0].foundation_scenario_id == "s1_1_x"
    assert candidates[0].scope == "property"
    assert candidates[0].scope_id == "prop-1"


def test_detect_higher_count_promotes_severity() -> None:
    """Five overrides ⇒ MEDIUM, ten ⇒ HIGH."""
    medium = detect_foundation_drift(
        [_make_case(foundation_scenario_id="s1_1_x") for _ in range(5)],
    )
    assert medium[0].severity == UpdateSeverity.MEDIUM
    high = detect_foundation_drift(
        [_make_case(foundation_scenario_id="s1_1_x") for _ in range(10)],
    )
    assert high[0].severity == UpdateSeverity.HIGH


def test_detect_groups_by_scenario_and_scope() -> None:
    """Different scenarios / scopes produce independent candidates."""
    cases = (
        [
            _make_case(
                foundation_scenario_id="s1_1_x",
                property_id="prop-1",
            )
            for _ in range(3)
        ]
        + [
            _make_case(
                foundation_scenario_id="s1_1_x",
                property_id="prop-2",
            )
            for _ in range(3)
        ]
        + [
            _make_case(
                foundation_scenario_id="s2_1_y",
                property_id="prop-1",
            )
            for _ in range(3)
        ]
    )
    candidates = detect_foundation_drift(cases)
    keys = {
        (c.foundation_scenario_id, c.scope_id)
        for c in candidates
    }
    assert keys == {
        ("s1_1_x", "prop-1"),
        ("s1_1_x", "prop-2"),
        ("s2_1_y", "prop-1"),
    }


def test_detect_order_is_deterministic() -> None:
    """Output ordering is ``(foundation_scenario_id, scope_id)`` ASC."""
    cases = (
        [
            _make_case(
                foundation_scenario_id="s2_1_y",
                property_id="prop-zebra",
            )
            for _ in range(3)
        ]
        + [
            _make_case(
                foundation_scenario_id="s1_1_x",
                property_id="prop-alpha",
            )
            for _ in range(3)
        ]
    )
    candidates = detect_foundation_drift(cases)
    ids = [(c.foundation_scenario_id, c.scope_id) for c in candidates]
    assert ids == sorted(ids)


def test_detect_owner_scope_uses_owner_id() -> None:
    """``scope="owner"`` keys on ``case.owner_id`` instead of property."""
    cases = [
        _make_case(
            foundation_scenario_id="s1_1_x",
            owner_id="owner-42",
        )
        for _ in range(3)
    ]
    candidates = detect_foundation_drift(cases, scope="owner")
    assert len(candidates) == 1
    assert candidates[0].scope == "owner"
    assert candidates[0].scope_id == "owner-42"


def test_detect_threshold_override() -> None:
    """A custom threshold of 5 needs five overrides."""
    cases = [
        _make_case(foundation_scenario_id="s1_1_x") for _ in range(4)
    ]
    assert detect_foundation_drift(cases, threshold=5) == ()
    more = [*cases, _make_case(foundation_scenario_id="s1_1_x")]
    candidates = detect_foundation_drift(more, threshold=5)
    assert len(candidates) == 1
    assert candidates[0].override_count == 5


def test_detect_source_case_ids_match_case_ids() -> None:
    """The candidate carries the case ids that triggered it."""
    cases = [
        _make_case(foundation_scenario_id="s1_1_x") for _ in range(3)
    ]
    expected_ids = tuple(c.case_id for c in cases)
    candidates = detect_foundation_drift(cases)
    assert candidates[0].source_case_ids == expected_ids


# ── batch helper ──────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_upsert_candidates_writes_all_and_returns_count() -> None:
    """``upsert_candidates`` writes every candidate through the store."""
    store = InMemoryFoundationUpdateStore()
    candidates = detect_foundation_drift(
        [_make_case(foundation_scenario_id="s1_1_x") for _ in range(3)]
        + [_make_case(foundation_scenario_id="s1_2_y") for _ in range(3)],
    )
    written = await upsert_candidates(store, candidates)
    assert written == 2
    assert len(await store.list_pending()) == 2


# ── default threshold sanity ──────────────────────────────── #


def test_default_threshold_matches_hospitality_md_guidance() -> None:
    """The default threshold mirrors the MD's "3+ comparable cases"."""
    assert DEFAULT_DRIFT_THRESHOLD == 3
