"""One-off backfill of temporal axes on historical ``decision_cases``.

Background
----------
Sprint 2 (`commit f9a4...`) extended ``CaseBuilder._build_pms_snapshot``
to write ``stage`` + ``hours_before_checkin`` into ``pms_snapshot``
so the ``ConditionSynthesizer`` (whose allowlist already grew the
keys) can mine temporal-axis splits like "PM defers when
``hours_before_checkin >= -120``".  Sprint 3b extended the
allowlist to surface the keys.

The change only fires for cases ingested *after* the deploy.  On
property 323133 alone there are 7 652 historical ``decision_cases``
written before the Sprint-2 cut over, none of which carry the new
keys — so the synthesiser never sees a temporal axis on the
property Mümin actually retests.

This script backfills the two derivable keys onto existing rows:

* ``stage`` — already a top-level column on ``decision_cases``;
  the script copies it into ``pms_snapshot["stage"]`` (string form)
  so the synthesiser allowlist surfaces it through ``_flatten``.
* ``hours_before_checkin`` — derived from
  ``pms_snapshot["check_in"]`` (a ``YYYY-MM-DD`` date string from
  the PMS feed) minus ``decision_cases.created_at``.  Negative
  values are valid (case logged *after* check-in) and the
  synthesiser handles them.

``lead_time_hours`` is **not** backfilled.  It needs the PMS
``reservation_created_at`` timestamp, which is not stored on the
``pms_snapshot`` payload — even forward-looking ingestion lands
``lead_time_hours == 0`` for most rows on dev (a separate gap).

Selection criteria are deliberately narrow:

* ``--scope-id`` is **required** so the operator opts in per
  property; no scope-wide sweep.
* Only rows missing ``stage`` *or* ``hours_before_checkin`` in
  ``pms_snapshot`` are considered.  Rows that already carry both
  keys are left alone — the script is idempotent.
* Rows whose ``pms_snapshot["check_in"]`` is missing, ``null``, or
  cannot be parsed as a date are tallied as ``unparseable`` and
  skipped (the script still backfills ``stage`` for them when the
  user passes ``--stage-only``).

Usage
-----
Dry-run (default — no writes; prints sample + summary)::

    python scripts/backfill_temporal_features.py --scope-id 323133

Apply once you have eyeballed the dry-run output::

    python scripts/backfill_temporal_features.py \\
        --scope-id 323133 --apply

Stage-only mode skips ``hours_before_checkin`` derivation and only
copies the ``stage`` column into ``pms_snapshot`` (useful for rows
whose ``check_in`` is unparseable)::

    python scripts/backfill_temporal_features.py \\
        --scope-id 323133 --stage-only

Environment
-----------
The script reads ``DECISION_CASE_STORE_DATABASE_URL`` first, then
falls back to ``DATABASE_URL`` — the same precedence the API
server's ``build_pattern_rule_store`` uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

# Make the project root importable when invoked directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncpg  # noqa: E402  -- after sys.path bootstrap
import structlog  # noqa: E402  -- after sys.path bootstrap

from brain_engine.integrations.unified_data import (  # noqa: E402
    RESERVATIONS_LIST_QUERY,
    UnifiedDataGraphQLClient,
)

logger = structlog.get_logger(__name__).bind(
    component="backfill_temporal_features",
)

_EXIT_OK: Final[int] = 0
_EXIT_ERROR: Final[int] = 1
_DEFAULT_BATCH_SIZE: Final[int] = 500
_SAMPLE_SIZE: Final[int] = 5

# Sprint 8 ext — GraphQL pagination knobs.  Page size matches the
# largest the onboarding-api accepts in one round-trip; the loop
# stops the first time a page returns fewer than ``_GQL_PAGE_SIZE``
# rows.  ``_GQL_MAX_PAGES`` is a defensive cap so a misbehaving
# upstream cannot turn the backfill into an infinite request loop.
_GQL_PAGE_SIZE: Final[int] = 1000
_GQL_MAX_PAGES: Final[int] = 200


# ---------------------------------------------------------------------------
# Pure helpers — derivation logic, fully unit-testable
# ---------------------------------------------------------------------------


def _parse_check_in(check_in: object) -> datetime | None:
    """Parse a PMS ``check_in`` field into an aware UTC datetime.

    Accepts ``YYYY-MM-DD`` (date-only) or ISO-8601 timestamps.
    Date-only inputs anchor to midnight UTC.  Returns ``None`` for
    missing, blank, or unparseable values — the caller treats that
    as "cannot derive ``hours_before_checkin``".
    """
    if not isinstance(check_in, str) or not check_in.strip():
        return None
    raw = check_in.strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(
                tzinfo=UTC,
            )
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hours_before_checkin(
    *,
    check_in: object,
    created_at: datetime,
) -> float | None:
    """Compute the temporal axis the synthesiser learns on.

    Args:
        check_in: Raw value from ``pms_snapshot["check_in"]`` —
            usually ``YYYY-MM-DD`` from the PMS feed.
        created_at: The ``decision_cases.created_at`` column —
            timezone-aware timestamp of when the decision was logged.

    Returns:
        ``(check_in_at_midnight_utc - created_at).total_seconds() / 3600``
        rounded to four decimals.  ``None`` when ``check_in`` is
        missing or unparseable.  Negative values mean the case was
        logged after check-in (in-stay or post-stay) and are valid.
    """
    parsed = _parse_check_in(check_in)
    if parsed is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    delta_seconds = (parsed - created_at).total_seconds()
    return round(delta_seconds / 3600.0, 4)


def _build_updated_snapshot(
    *,
    pms_snapshot: dict[str, object],
    stage: str,
    hours_before: float | None,
    lead_time: float | None = None,
) -> dict[str, object]:
    """Return a new snapshot dict with backfilled keys.

    The original snapshot is not mutated (defensive copy).  Keys
    already present are preserved verbatim; new keys are appended.
    When ``hours_before`` is ``None`` the snapshot only gains the
    ``stage`` key — the script tolerates the partial backfill so
    rows with unparseable ``check_in`` are not blocked.

    Args:
        pms_snapshot: Current ``decision_cases.pms_snapshot`` JSONB.
        stage: ``decision_cases.stage`` column value (string form).
        hours_before: Computed temporal axis or ``None`` when
            ``check_in`` is unparseable.
        lead_time: Computed lead-time axis (Sprint 8 ext, GraphQL
            ``reservation.data.createdAt`` minus arrival).  ``None``
            when the GraphQL fan-out was disabled or the case's
            reservation_id had no upstream record.
    """
    updated = dict(pms_snapshot)
    updated.setdefault("stage", stage)
    if hours_before is not None:
        updated.setdefault("hours_before_checkin", hours_before)
    if lead_time is not None:
        updated.setdefault("lead_time_hours", lead_time)
    return updated


def _lead_time_hours(
    *,
    check_in: object,
    reservation_created_at: datetime | None,
) -> float | None:
    """Hours between reservation creation and arrival.

    Args:
        check_in: Raw value from ``pms_snapshot["check_in"]``.
        reservation_created_at: ``reservation.data.createdAt`` parsed
            from the GraphQL response.

    Returns:
        ``(arrival_at_midnight_utc - reservation_created_at)`` divided
        by 3600 and rounded to four decimals.  ``None`` when either
        input is missing or unparseable.  Negative values are
        physically impossible (booked after check-in) and are
        clamped to ``0.0`` so the synthesiser never sees a bogus
        negative training signal.
    """
    if reservation_created_at is None:
        return None
    arrival = _parse_check_in(check_in)
    if arrival is None:
        return None
    if reservation_created_at.tzinfo is None:
        reservation_created_at = reservation_created_at.replace(tzinfo=UTC)
    delta_seconds = (arrival - reservation_created_at).total_seconds()
    return round(max(delta_seconds / 3600.0, 0.0), 4)


def _extract_reservation_index_entries(
    page: dict[str, object],
) -> list[tuple[str, datetime]]:
    """Project one ``reservations`` page into ``(key, createdAt)`` pairs.

    The GraphQL response carries multiple identifier fields per
    reservation (``id``, ``channelEntityId``, ``customerChannelId``,
    ``pmsId`` plus ``data.pmsId``).  ``decision_cases.reservation_id``
    historically resolves to the *PMS* identifier, so we index every
    plausible identifier and let lookup take whichever matches.

    Returns an empty list when the page payload is malformed — the
    caller logs a warning and continues so a single bad page cannot
    abort the backfill.
    """
    rows = page.get("reservations") if isinstance(page, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, datetime]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        created_raw = (
            data.get("createdAt") if isinstance(data, dict) else None
        )
        created = _parse_check_in(created_raw)
        if created is None:
            continue
        for key_field in (
            "pmsId",
            "channelEntityId",
            "customerChannelId",
            "id",
        ):
            key = row.get(key_field)
            if isinstance(key, str) and key:
                out.append((key, created))
        if isinstance(data, dict):
            inner_pms = data.get("pmsId")
            if isinstance(inner_pms, str) and inner_pms:
                out.append((inner_pms, created))
    return out


async def _fetch_reservation_created_index(
    client: UnifiedDataGraphQLClient,
    *,
    customer_id: str,
    org_id: str | None,
    provider_type: str | None,
    property_channel_id: str,
) -> dict[str, datetime]:
    """Build ``{reservation_id: createdAt}`` for one property scope.

    Paginates ``RESERVATIONS_LIST_QUERY`` with the property filter
    set so the script never hauls the tenant-wide reservation list.
    The first page that returns fewer rows than ``_GQL_PAGE_SIZE``
    closes the loop; ``_GQL_MAX_PAGES`` is a hard upper bound that
    only matters if the upstream returns full pages indefinitely.
    """
    index: dict[str, datetime] = {}
    skip = 0
    for _ in range(_GQL_MAX_PAGES):
        variables: dict[str, object] = {
            "customerId": customer_id,
            "propertyChannelId": property_channel_id,
            "limit": _GQL_PAGE_SIZE,
            "skip": skip,
        }
        if org_id:
            variables["orgId"] = org_id
        if provider_type:
            variables["providerType"] = provider_type
        page = await client.execute(
            RESERVATIONS_LIST_QUERY,
            variables,
            operation_name="Reservations",
        )
        entries = _extract_reservation_index_entries(page)
        if not entries:
            break
        for key, created in entries:
            index.setdefault(key, created)
        rows = page.get("reservations")
        page_count = (
            len(rows)
            if isinstance(rows, list)
            else 0
        )
        if page_count < _GQL_PAGE_SIZE:
            break
        skip += _GQL_PAGE_SIZE
    return index


def _snapshot_needs_update(
    *,
    pms_snapshot: dict[str, object],
    stage_only: bool,
    with_lead_time: bool = False,
) -> bool:
    """Whether a row's snapshot is missing at least one backfill key.

    Args:
        pms_snapshot: Current ``decision_cases.pms_snapshot`` JSONB.
        stage_only: When ``True`` the only required key is ``stage``;
            ``hours_before_checkin`` and ``lead_time_hours`` are
            ignored.
        with_lead_time: Sprint 8 ext flag — when ``True`` the row
            also qualifies for backfill if ``lead_time_hours`` is
            missing, even when stage and hours_before_checkin are
            already populated.
    """
    if "stage" not in pms_snapshot:
        return True
    if stage_only:
        return False
    if "hours_before_checkin" not in pms_snapshot:
        return True
    return with_lead_time and "lead_time_hours" not in pms_snapshot


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Aggregate outcome of one script invocation."""

    inspected: int
    selected: int
    updated: int
    skipped_already_complete: int
    skipped_unparseable_check_in: int
    backfilled_stage_only: int
    by_scenario: dict[str, int] = field(default_factory=dict)
    # Sprint 8 ext — populated only when ``--with-lead-time`` was set.
    # Counts how many of the selected rows received a non-``None``
    # ``lead_time_hours`` value (the rest had no matching reservation
    # in the GraphQL index, e.g. ``decision_cases.reservation_id`` was
    # NULL or upstream had no record).  The reservation-index size is
    # also surfaced so the operator can sanity-check pagination.
    reservation_index_size: int = 0
    backfilled_lead_time: int = 0


# ---------------------------------------------------------------------------
# Database I/O
# ---------------------------------------------------------------------------


def _resolve_database_url() -> str:
    """Pick the same DATABASE_URL the API uses."""
    url = (
        os.environ.get("DECISION_CASE_STORE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise ValueError(
            "DECISION_CASE_STORE_DATABASE_URL or DATABASE_URL must be set",
        )
    return url


async def _fetch_rows(
    pool: asyncpg.Pool,
    *,
    scope_id: str,
    stage_only: bool,
    with_lead_time: bool = False,
) -> list[asyncpg.Record]:
    """Read candidate rows for one scope.

    Filter at SQL time so we only haul rows whose snapshot is
    missing at least one backfill key.  Mirrors the in-Python
    ``_snapshot_needs_update`` predicate exactly so the in-Python
    pass cannot diverge from the row set the SQL surfaces.
    """
    query = """
        SELECT id::text AS id,
               case_id,
               scenario,
               stage,
               reservation_id,
               pms_snapshot,
               created_at
        FROM decision_cases
        WHERE property_id = $1
          AND (
              NOT (pms_snapshot ? 'stage')
              OR (
                  $2::boolean = false
                  AND NOT (pms_snapshot ? 'hours_before_checkin')
              )
              OR (
                  $3::boolean = true
                  AND $2::boolean = false
                  AND NOT (pms_snapshot ? 'lead_time_hours')
              )
          )
        ORDER BY created_at ASC
    """
    return await pool.fetch(query, scope_id, stage_only, with_lead_time)


async def _apply_updates(
    pool: asyncpg.Pool,
    *,
    updates: Sequence[tuple[str, dict[str, object]]],
    batch_size: int,
) -> int:
    """Persist the new snapshots one batch at a time.

    Each update is keyed by the row UUID so concurrent inserts on
    the table (the live brain-engine ingestion loop) cannot race
    with the backfill.  Returns the count of rows actually
    UPDATEd — should equal ``len(updates)`` unless a row was
    deleted between fetch and apply.
    """
    if not updates:
        return 0
    written = 0
    for offset in range(0, len(updates), batch_size):
        chunk = updates[offset:offset + batch_size]
        async with pool.acquire() as conn, conn.transaction():
            for row_id, snapshot in chunk:
                payload = json.dumps(snapshot)
                await conn.execute(
                    "UPDATE decision_cases "
                    "SET pms_snapshot = $2::jsonb, updated_at = now() "
                    "WHERE id = $1::uuid",
                    row_id,
                    payload,
                )
                written += 1
    return written


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def _build_reservation_index(
    *,
    scope_id: str,
    with_lead_time: bool,
) -> dict[str, datetime]:
    """Wrap the GraphQL fan-out behind the ``--with-lead-time`` flag.

    Returns an empty dict and silently logs when the flag is off
    so callers can unconditionally ``map.get(...)``.  Reads the
    same env vars the API uses (``UNIFIED_DATA_BASE_URL``,
    ``UNIFIED_DATA_CUSTOMER_ID``, ``UNIFIED_DATA_ORG_ID``,
    ``UNIFIED_DATA_PROVIDER_TYPE``, ``UNIFIED_DATA_AUTH_TOKEN``).
    """
    if not with_lead_time:
        return {}
    customer_id = os.environ.get("UNIFIED_DATA_CUSTOMER_ID", "").strip()
    if not customer_id:
        raise ValueError(
            "UNIFIED_DATA_CUSTOMER_ID must be set when "
            "--with-lead-time is requested",
        )
    org_id = os.environ.get("UNIFIED_DATA_ORG_ID", "").strip() or None
    provider_type = (
        os.environ.get("UNIFIED_DATA_PROVIDER_TYPE", "").strip() or None
    )
    base_url = os.environ.get("UNIFIED_DATA_BASE_URL", "").strip()
    auth_token = (
        os.environ.get("UNIFIED_DATA_AUTH_TOKEN", "").strip() or None
    )
    client_kwargs: dict[str, object] = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    if auth_token:
        client_kwargs["auth_token"] = auth_token
    async with UnifiedDataGraphQLClient(**client_kwargs) as client:
        return await _fetch_reservation_created_index(
            client,
            customer_id=customer_id,
            org_id=org_id,
            provider_type=provider_type,
            property_channel_id=scope_id,
        )


async def _run(
    *,
    scope_id: str,
    apply: bool,
    stage_only: bool,
    batch_size: int,
    with_lead_time: bool = False,
) -> BackfillReport:
    """Execute the backfill pass and return an aggregate report."""
    url = _resolve_database_url()
    reservation_index = await _build_reservation_index(
        scope_id=scope_id,
        with_lead_time=with_lead_time,
    )
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        rows = await _fetch_rows(
            pool,
            scope_id=scope_id,
            stage_only=stage_only,
            with_lead_time=with_lead_time,
        )

        selected: list[tuple[str, dict[str, object]]] = []
        skipped_unparseable = 0
        partial_stage_only = 0
        lead_time_hits = 0
        by_scenario: dict[str, int] = {}

        for row in rows:
            existing_raw = row["pms_snapshot"]
            existing = (
                json.loads(existing_raw)
                if isinstance(existing_raw, str)
                else dict(existing_raw or {})
            )
            if not _snapshot_needs_update(
                pms_snapshot=existing,
                stage_only=stage_only,
                with_lead_time=with_lead_time,
            ):
                continue
            stage_value = row["stage"]
            hours = (
                None
                if stage_only
                else _hours_before_checkin(
                    check_in=existing.get("check_in"),
                    created_at=row["created_at"],
                )
            )
            reservation_id = row.get("reservation_id")
            lead_time = (
                _lead_time_hours(
                    check_in=existing.get("check_in"),
                    reservation_created_at=reservation_index.get(
                        str(reservation_id),
                    ),
                )
                if (
                    with_lead_time
                    and "lead_time_hours" not in existing
                    and reservation_id
                )
                else None
            )
            if lead_time is not None:
                lead_time_hits += 1
            if not stage_only and hours is None:
                skipped_unparseable += 1
                if "stage" in existing and lead_time is None:
                    # Stage already there, hours unparseable, no
                    # lead-time addition either — nothing left to
                    # write for this row.
                    continue
                if "stage" not in existing:
                    partial_stage_only += 1
            updated = _build_updated_snapshot(
                pms_snapshot=existing,
                stage=str(stage_value),
                hours_before=hours,
                lead_time=lead_time,
            )
            # Skip no-op writes — happens when ``--with-lead-time`` is
            # set, the SQL filter admits a row missing only
            # ``lead_time_hours``, but the GraphQL index has no
            # ``createdAt`` for its reservation_id.  ``setdefault``
            # keeps every existing key, so the new dict is structurally
            # identical to ``existing`` and an UPDATE would touch
            # ``updated_at`` for no payload reason.
            if updated == existing:
                continue
            selected.append((row["id"], updated))
            label = str(row["scenario"])
            by_scenario[label] = by_scenario.get(label, 0) + 1

        # Print up to 5 sample rows for human eyeballing.
        for row_id, snapshot in selected[:_SAMPLE_SIZE]:
            mode = "DRY-RUN" if not apply else "APPLY"
            print(
                f"[{mode}] row={row_id[:8]} "
                f"stage={snapshot.get('stage')!r} "
                f"hours_before_checkin="
                f"{snapshot.get('hours_before_checkin')!r} "
                f"lead_time_hours={snapshot.get('lead_time_hours')!r}",
            )
        if len(selected) > _SAMPLE_SIZE:
            print(
                f"[...] {len(selected) - _SAMPLE_SIZE} more rows "
                f"queued for backfill",
            )

        updated_count = (
            await _apply_updates(
                pool,
                updates=selected,
                batch_size=batch_size,
            )
            if apply
            else 0
        )

        return BackfillReport(
            inspected=len(rows),
            selected=len(selected),
            updated=updated_count,
            skipped_already_complete=len(rows) - len(selected),
            skipped_unparseable_check_in=skipped_unparseable,
            backfilled_stage_only=partial_stage_only,
            reservation_index_size=len(reservation_index),
            backfilled_lead_time=lead_time_hits,
            by_scenario=by_scenario,
        )
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.  Default mode is dry-run."""
    parser = argparse.ArgumentParser(
        description=(
            "Backfill ``stage`` + ``hours_before_checkin`` keys on "
            "historical decision_cases.pms_snapshot rows.  Default "
            "is dry-run; --scope-id is required."
        ),
    )
    parser.add_argument(
        "--scope-id",
        type=str,
        required=True,
        help=(
            "Property id (or other scope id) to sweep.  Required so "
            "the operator opts in per scope; there is no scope-wide "
            "sweep."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually write the new snapshots.  Without this flag "
            "the script only prints what it would do and returns "
            "the same report counts (updated=0)."
        ),
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help=(
            "Only copy the ``stage`` column into pms_snapshot; skip "
            "``hours_before_checkin`` derivation entirely.  Useful "
            "for batches whose ``check_in`` is unparseable."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=(
            "Number of rows updated per transaction.  Larger "
            "batches reduce round-trips, smaller batches release "
            "row locks sooner.  Default %(default)d."
        ),
    )
    parser.add_argument(
        "--with-lead-time",
        action="store_true",
        help=(
            "Sprint 8 ext — additionally derive "
            "``lead_time_hours`` from ``reservation.data.createdAt`` "
            "via the onboarding-api unified GraphQL gateway.  "
            "Requires ``UNIFIED_DATA_CUSTOMER_ID`` (and optionally "
            "``UNIFIED_DATA_BASE_URL`` / ``UNIFIED_DATA_AUTH_TOKEN`` "
            "/ ``UNIFIED_DATA_ORG_ID`` / "
            "``UNIFIED_DATA_PROVIDER_TYPE``) — same env surface the "
            "API server reads.  Mutually exclusive with "
            "``--stage-only``."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns a Unix exit code."""
    args = _parse_args(argv)
    if args.batch_size < 1:
        print("[FAIL] --batch-size must be >= 1", file=sys.stderr)
        return _EXIT_ERROR
    if args.stage_only and args.with_lead_time:
        print(
            "[FAIL] --stage-only and --with-lead-time are mutually exclusive",
            file=sys.stderr,
        )
        return _EXIT_ERROR
    try:
        report = asyncio.run(
            _run(
                scope_id=args.scope_id,
                apply=args.apply,
                stage_only=args.stage_only,
                batch_size=args.batch_size,
                with_lead_time=args.with_lead_time,
            ),
        )
    except (ValueError, OSError, ConnectionError) as exc:
        logger.error(
            "backfill_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_ERROR

    mode = "apply" if args.apply else "dry-run"
    print()
    summary_parts = [
        f"[SUMMARY] mode={mode}",
        f"scope_id={args.scope_id}",
        f"inspected={report.inspected}",
        f"selected={report.selected}",
        f"updated={report.updated}",
        f"skipped_already_complete={report.skipped_already_complete}",
        (
            "skipped_unparseable_check_in="
            f"{report.skipped_unparseable_check_in}"
        ),
        f"backfilled_stage_only={report.backfilled_stage_only}",
    ]
    if args.with_lead_time:
        summary_parts.extend(
            [
                f"reservation_index_size={report.reservation_index_size}",
                f"backfilled_lead_time={report.backfilled_lead_time}",
            ],
        )
    print(" ".join(summary_parts))
    if report.by_scenario:
        for scenario_label, count in sorted(report.by_scenario.items()):
            print(f"  scenario={scenario_label}: {count}")
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
