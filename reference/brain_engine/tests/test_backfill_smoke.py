"""Local smoke harness for the Sprint 8 backfill script.

The 25 unit tests in ``test_backfill_temporal_features.py`` pin
the pure derivation pipeline.  This file exercises the *whole*
``_run`` coroutine end-to-end with ``asyncpg`` monkey-patched to
serve a hand-built fixture set.  Goal: verify the orchestrator
contract before ever touching the dev cluster — does the
dry-run path skip writes, does the apply path land them, do the
report counts agree with the fixture?

The ``MockPool`` here intentionally does **not** model the full
``asyncpg.Pool`` API — only the entry points the script calls
(``fetch``, ``acquire``, ``transaction``, ``execute``, ``close``).
Anything else would either signal that the script grew a new I/O
shape worth re-reviewing or that the mock drifted from the real
library — both are signals worth a test failure.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts.backfill_temporal_features import _run

# ---------------------------------------------------------------------------
# Mock asyncpg surface
# ---------------------------------------------------------------------------


class _MockConnection:
    """Stub for an asyncpg connection used inside ``_apply_updates``."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(
        self,
        query: str,
        row_id: str,
        payload: str,
    ) -> None:
        self.executed.append((query, row_id, payload))


class _MockPool:
    """Stub for ``asyncpg.Pool`` covering only the calls the script uses."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.connection = _MockConnection()
        self.fetch_calls: list[tuple[str, bool, bool]] = []
        self.closed = False

    async def fetch(
        self,
        query: str,
        scope_id: str,
        stage_only: bool,
        with_lead_time: bool = False,
    ) -> list[dict[str, Any]]:
        self.fetch_calls.append((scope_id, stage_only, with_lead_time))
        # Apply the same WHERE-clause filter the SQL would do.  This
        # guarantees the mock can't accidentally serve rows that the
        # production query would skip — the smoke would otherwise
        # report a different ``inspected`` count than reality.
        out: list[dict[str, Any]] = []
        for row in self._rows:
            if row["property_id"] != scope_id:
                continue
            snapshot = row["pms_snapshot"]
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)
            has_stage = "stage" in snapshot
            has_hours = "hours_before_checkin" in snapshot
            has_lead = "lead_time_hours" in snapshot
            if not has_stage:
                out.append(row)
                continue
            if stage_only:
                continue
            if not has_hours:
                out.append(row)
                continue
            if with_lead_time and not has_lead:
                out.append(row)
        return out

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_MockConnection]:
        yield self.connection

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fixture_rows() -> list[dict[str, Any]]:
    """Six rows on property 323133 covering each branch of the script.

    Row shapes mirror what ``_fetch_rows`` selects:
    ``id``, ``case_id``, ``scenario``, ``stage``, ``pms_snapshot``,
    ``created_at``.  The ``property_id`` is added for the mock's
    own filtering (the production SQL filters at the DB).
    """
    return [
        {
            # 1. Pre-Sprint row — needs both keys backfilled.
            "id": "11111111-1111-1111-1111-111111111111",
            "case_id": "case-1",
            "property_id": "323133",
            "scenario": "access_code_release",
            "stage": "pre_arrival",
            "reservation_id": "R-1",
            "pms_snapshot": {"adults": 2, "check_in": "2026-05-12"},
            "created_at": datetime(2026, 5, 5, 0, 0, tzinfo=UTC),
        },
        {
            # 2. Post-Sprint row — already complete; should be skipped
            # at the SQL layer (mock mimics the WHERE clause).
            "id": "22222222-2222-2222-2222-222222222222",
            "case_id": "case-2",
            "property_id": "323133",
            "scenario": "access_code_release",
            "stage": "in_stay",
            "reservation_id": "R-2",
            "pms_snapshot": {
                "adults": 1,
                "check_in": "2026-05-08",
                "stage": "in_stay",
                "hours_before_checkin": -24.0,
            },
            "created_at": datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        },
        {
            # 3. Row with stage already populated but missing
            # hours_before_checkin — backfill only the second key.
            "id": "33333333-3333-3333-3333-333333333333",
            "case_id": "case-3",
            "property_id": "323133",
            "scenario": "amenity_exception",
            "stage": "pre_arrival",
            "reservation_id": "R-3",
            "pms_snapshot": {
                "adults": 3,
                "check_in": "2026-06-01",
                "stage": "pre_arrival",
            },
            "created_at": datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
        },
        {
            # 4. Row whose check_in is unparseable — only stage can
            # be derived; ``hours_before_checkin`` stays absent.
            "id": "44444444-4444-4444-4444-444444444444",
            "case_id": "case-4",
            "property_id": "323133",
            "scenario": "cancellation_request",
            "stage": "post_stay",
            "reservation_id": "R-4",
            "pms_snapshot": {"adults": 1, "check_in": "garbage"},
            "created_at": datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        },
        {
            # 5. Row missing check_in altogether — same handling as #4.
            "id": "55555555-5555-5555-5555-555555555555",
            "case_id": "case-5",
            "property_id": "323133",
            "scenario": "cancellation_request",
            "stage": "pre_booking",
            "reservation_id": None,
            "pms_snapshot": {"adults": 2},
            "created_at": datetime(2026, 4, 1, 0, 0, tzinfo=UTC),
        },
        {
            # 6. Different property — must NOT be returned for
            # ``--scope-id 323133``.
            "id": "66666666-6666-6666-6666-666666666666",
            "case_id": "case-6",
            "property_id": "309384",
            "scenario": "access_code_release",
            "stage": "pre_arrival",
            "reservation_id": "R-6",
            "pms_snapshot": {"adults": 1, "check_in": "2026-05-20"},
            "created_at": datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
        },
    ]


@pytest.fixture(autouse=True)
def _set_dummy_database_url() -> Iterator[None]:
    """Provide a value so ``_resolve_database_url`` does not raise.

    The mock pool ignores the URL — only its presence matters.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql://stub@stub/stub"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_dry_run_does_not_write(
    fixture_rows: list[dict[str, Any]],
    monkeypatch,
) -> None:
    pool = _MockPool(fixture_rows)

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _MockPool:
        return pool

    monkeypatch.setattr(
        "scripts.backfill_temporal_features.asyncpg.create_pool",
        _fake_create_pool,
    )
    report = await _run(
        scope_id="323133",
        apply=False,
        stage_only=False,
        batch_size=500,
    )

    # Mock only returned rows that need work (ids 1, 3, 4, 5);
    # row 2 was filtered out at fetch time, row 6 is wrong scope.
    assert report.inspected == 4
    # Selected = rows for which we can write *something*:
    #   row 1 — both keys derivable
    #   row 3 — only hours_before_checkin missing, derivable
    #   row 4 — only stage derivable (check_in unparseable)
    #   row 5 — only stage derivable (no check_in)
    assert report.selected == 4
    # Dry-run never writes.
    assert report.updated == 0
    # Two rows had unparseable / missing check_in.
    assert report.skipped_unparseable_check_in == 2
    # Both of those (rows 4 + 5) had no pre-existing stage either,
    # so the partial backfill is recorded.
    assert report.backfilled_stage_only == 2
    # Pool was used and closed cleanly.
    assert pool.fetch_calls == [("323133", False, False)]
    assert pool.closed is True
    # And critically — the connection's execute path was never
    # touched in dry-run mode.
    assert pool.connection.executed == []


@pytest.mark.asyncio
async def test_smoke_apply_writes_each_selected_row(
    fixture_rows: list[dict[str, Any]],
    monkeypatch,
) -> None:
    pool = _MockPool(fixture_rows)

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _MockPool:
        return pool

    monkeypatch.setattr(
        "scripts.backfill_temporal_features.asyncpg.create_pool",
        _fake_create_pool,
    )
    report = await _run(
        scope_id="323133",
        apply=True,
        stage_only=False,
        batch_size=500,
    )

    assert report.selected == 4
    assert report.updated == 4
    # One UPDATE per selected row; queries carry the new snapshot.
    assert len(pool.connection.executed) == 4
    queries = {entry[0] for entry in pool.connection.executed}
    assert queries == {
        "UPDATE decision_cases "
        "SET pms_snapshot = $2::jsonb, updated_at = now() "
        "WHERE id = $1::uuid",
    }
    # Spot check row 1 payload — both keys backfilled.
    payloads = {
        entry[1]: json.loads(entry[2])
        for entry in pool.connection.executed
    }
    row_1 = payloads["11111111-1111-1111-1111-111111111111"]
    assert row_1["stage"] == "pre_arrival"
    assert row_1["hours_before_checkin"] == 168.0
    # Spot check row 4 payload — only stage backfilled.
    row_4 = payloads["44444444-4444-4444-4444-444444444444"]
    assert row_4["stage"] == "post_stay"
    assert "hours_before_checkin" not in row_4


@pytest.mark.asyncio
async def test_smoke_stage_only_skips_hours_derivation(
    fixture_rows: list[dict[str, Any]],
    monkeypatch,
) -> None:
    pool = _MockPool(fixture_rows)

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _MockPool:
        return pool

    monkeypatch.setattr(
        "scripts.backfill_temporal_features.asyncpg.create_pool",
        _fake_create_pool,
    )
    report = await _run(
        scope_id="323133",
        apply=False,
        stage_only=True,
        batch_size=500,
    )

    # In stage-only mode the SQL filter only selects rows missing
    # ``stage``.  Rows 1, 4, 5 qualify; row 3 is already stage-set.
    assert report.inspected == 3
    assert report.selected == 3
    # With stage_only=True, ``skipped_unparseable_check_in`` is
    # always 0 because the script never tries to derive hours.
    assert report.skipped_unparseable_check_in == 0
    assert pool.fetch_calls == [("323133", True, False)]
    assert pool.closed is True


# ---------------------------------------------------------------------------
# Sprint 8 ext — ``--with-lead-time`` smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_with_lead_time_uses_graphql_index(
    fixture_rows: list[dict[str, Any]],
    monkeypatch,
) -> None:
    """``--with-lead-time`` re-includes complete rows for lead-time backfill.

    Row 2 already carries stage + hours_before_checkin (the
    pre-Sprint-8-ext "complete" set), but lacks ``lead_time_hours``.
    With the flag on, the SQL filter re-admits it and the GraphQL
    index resolves a ``createdAt`` for ``reservation_id=R-2``.
    """
    pool = _MockPool(fixture_rows)

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _MockPool:
        return pool

    monkeypatch.setattr(
        "scripts.backfill_temporal_features.asyncpg.create_pool",
        _fake_create_pool,
    )

    async def _fake_index(
        *,
        scope_id: str,
        with_lead_time: bool,
    ) -> dict[str, datetime]:
        return {"R-2": datetime(2026, 5, 1, 0, 0, tzinfo=UTC)}

    monkeypatch.setattr(
        "scripts.backfill_temporal_features._build_reservation_index",
        _fake_index,
    )
    report = await _run(
        scope_id="323133",
        apply=True,
        stage_only=False,
        batch_size=500,
        with_lead_time=True,
    )

    # Reservation index has only R-2; only that row gets a
    # lead-time addition.  Other rows still backfill stage / hours.
    assert report.reservation_index_size == 1
    assert report.backfilled_lead_time == 1
    assert pool.fetch_calls == [("323133", False, True)]

    payloads = {
        entry[1]: json.loads(entry[2])
        for entry in pool.connection.executed
    }
    row_2 = payloads["22222222-2222-2222-2222-222222222222"]
    # 7 days * 24h = 168h between created_at 2026-05-01 -> arrival
    # 2026-05-08.
    assert row_2["lead_time_hours"] == pytest.approx(168.0)
    # Pre-existing keys preserved verbatim.
    assert row_2["stage"] == "in_stay"
    assert row_2["hours_before_checkin"] == -24.0


@pytest.mark.asyncio
async def test_smoke_with_lead_time_empty_index_no_lead_writes(
    fixture_rows: list[dict[str, Any]],
    monkeypatch,
) -> None:
    """Empty GraphQL index → no row receives ``lead_time_hours``."""
    pool = _MockPool(fixture_rows)

    async def _fake_create_pool(*_args: object, **_kwargs: object) -> _MockPool:
        return pool

    monkeypatch.setattr(
        "scripts.backfill_temporal_features.asyncpg.create_pool",
        _fake_create_pool,
    )

    async def _empty_index(
        *,
        scope_id: str,
        with_lead_time: bool,
    ) -> dict[str, datetime]:
        return {}

    monkeypatch.setattr(
        "scripts.backfill_temporal_features._build_reservation_index",
        _empty_index,
    )
    report = await _run(
        scope_id="323133",
        apply=True,
        stage_only=False,
        batch_size=500,
        with_lead_time=True,
    )

    assert report.reservation_index_size == 0
    assert report.backfilled_lead_time == 0
    payloads = {
        entry[1]: json.loads(entry[2])
        for entry in pool.connection.executed
    }
    # Row 2 was admitted via the with_lead_time SQL branch but the
    # in-Python pipeline early-continues when no new key can be
    # added — so no UPDATE was issued for it.
    assert "22222222-2222-2222-2222-222222222222" not in payloads
