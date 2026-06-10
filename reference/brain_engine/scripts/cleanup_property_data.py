"""One-shot cleanup that wipes every per-property row Brain Engine carries.

Usage
-----
Dry-run (default — no writes; prints row counts that *would* be
deleted)::

    python scripts/cleanup_property_data.py --property-id 323133

Apply (writes happen — irreversible)::

    python scripts/cleanup_property_data.py --property-id 323133 --apply

The script targets every Postgres table the API server reads /
writes that carries a ``property_id`` (or ``scope_id`` for the
property-scoped variant of ``pattern_rules``).  Tables without a
property column (``checkpoints``, ``event_sequencer``) are left
alone — they are not property-scoped.

Connection
----------
Reuses the same ``DATABASE_URL`` / ``DECISION_CASE_STORE_DATABASE_URL``
env var the API uses.  Run from a workstation that already has
network access to the Azure Postgres instance (e.g. via the dev
bastion or with the ingress port-forward open).

Honest scope
------------
- Postgres surface only.  Redis ``conv:{property_id}:*`` keys
  and Qdrant memory points are NOT touched by this script — flush
  them separately with ``redis-cli`` / the Qdrant admin API if the
  ask is "wipe the property end-to-end".  The script prints the
  exact one-liners to run.
- Cascade DELETE is not used; each table is purged explicitly so
  the operator can audit the count per table in the dry-run log.
- The script does not run inside a single transaction.  A
  partial failure leaves the cleanup half-applied; re-running the
  script is idempotent and finishes the job.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg
import structlog


logger = structlog.get_logger(__name__)


# (table, column) tuples ordered from leaf tables to root tables so
# foreign-key constraints (where they exist) clear cleanly.  When in
# doubt we delete from the dependent table first.
#
# Column names verified against the live dev schema on 2026-05-11.
# Tables that share the same property identifier under a different
# column name (``property_pm_facts`` / ``property_profiles`` use
# ``property_channel_id`` — the short Hostaway channel id, which is
# how Brain Engine identifies properties throughout the runtime)
# are enumerated explicitly so the operator does not have to know
# each table's quirks.  ``guest_memories`` and the A/B promotion
# tables (``ab_outcomes`` / ``ab_experiments``) are intentionally
# absent — the first does not carry a per-property key (its
# ``property_history`` column is JSONB metadata, not a foreign
# reference), the second pair does not exist in this deployment.
_PROPERTY_TABLES: tuple[tuple[str, str], ...] = (
    ("decision_cases", "property_id"),
    ("decision_cards", "property_id"),
    ("blockers", "property_id"),
    ("interactions", "property_id"),
    ("unanswered_threads", "property_id"),
    ("interview_answers", "property_id"),
    ("workflow_autonomy", "property_id"),
    ("owner_flexibility_profiles", "property_id"),
    ("property_pm_facts", "property_channel_id"),
    ("property_profiles", "property_channel_id"),
)

# pattern_rules uses ``scope_id`` for property-scoped rules; we
# additionally narrow on ``scope = 'property'`` so we never touch a
# global or owner-level rule that happens to share the id space.
_PATTERN_RULES_TABLE: tuple[str, str, str] = (
    "pattern_rules",
    "scope_id",
    "property",
)


@dataclass(frozen=True, slots=True)
class TableCleanupResult:
    """Per-table outcome of one cleanup pass."""

    table: str
    rows_matched: int
    rows_deleted: int


async def _count_rows(
    conn: asyncpg.Connection,
    table: str,
    column: str,
    property_id: str,
    extra_clause: str = "",
) -> int:
    """Return the number of rows currently matching the filter."""
    query = (
        f"SELECT COUNT(*) FROM {table} WHERE {column} = $1"
        f"{extra_clause}"
    )
    return int(await conn.fetchval(query, property_id))


async def _delete_rows(
    conn: asyncpg.Connection,
    table: str,
    column: str,
    property_id: str,
    extra_clause: str = "",
) -> int:
    """Delete rows for ``property_id`` and return the affected count."""
    query = (
        f"DELETE FROM {table} WHERE {column} = $1{extra_clause}"
    )
    status = await conn.execute(query, property_id)
    # asyncpg returns "DELETE <n>" on success.
    return int(status.split(" ")[-1])


async def cleanup_property(
    *,
    property_id: str,
    apply: bool,
) -> Sequence[TableCleanupResult]:
    """Run the cleanup for a single property.

    The connection string is read from environment variables in the
    same order the runtime uses, so the script picks up whatever the
    operator's shell session has already configured.
    """
    dsn = (
        os.environ.get("DECISION_CASE_STORE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        raise SystemExit(
            "DATABASE_URL not set — export the dev Postgres DSN "
            "before running this script.",
        )
    log = logger.bind(
        property_id=property_id, apply=apply,
    )
    results: list[TableCleanupResult] = []
    conn = await asyncpg.connect(dsn)
    try:
        # Per-property tables — all share the same column name.
        for table, column in _PROPERTY_TABLES:
            try:
                matched = await _count_rows(
                    conn, table, column, property_id,
                )
            except asyncpg.PostgresError as exc:
                log.warning(
                    "cleanup.table.skip",
                    table=table,
                    error=str(exc),
                )
                continue
            deleted = 0
            if apply and matched > 0:
                deleted = await _delete_rows(
                    conn, table, column, property_id,
                )
            results.append(
                TableCleanupResult(
                    table=table,
                    rows_matched=matched,
                    rows_deleted=deleted,
                )
            )
            log.info(
                "cleanup.table.scanned",
                table=table,
                rows_matched=matched,
                rows_deleted=deleted,
            )

        # pattern_rules — narrow on scope=property.
        rules_table, rules_column, rules_scope = _PATTERN_RULES_TABLE
        scope_clause = f" AND scope = '{rules_scope}'"
        try:
            matched = await _count_rows(
                conn,
                rules_table,
                rules_column,
                property_id,
                extra_clause=scope_clause,
            )
            deleted = 0
            if apply and matched > 0:
                deleted = await _delete_rows(
                    conn,
                    rules_table,
                    rules_column,
                    property_id,
                    extra_clause=scope_clause,
                )
            results.append(
                TableCleanupResult(
                    table=rules_table,
                    rows_matched=matched,
                    rows_deleted=deleted,
                )
            )
            log.info(
                "cleanup.table.scanned",
                table=rules_table,
                rows_matched=matched,
                rows_deleted=deleted,
            )
        except asyncpg.PostgresError as exc:
            log.warning(
                "cleanup.table.skip",
                table=rules_table,
                error=str(exc),
            )
    finally:
        await conn.close()
    return results


def _format_report(
    results: Sequence[TableCleanupResult],
    *,
    apply: bool,
) -> str:
    """Render a human-readable summary table."""
    header_action = "DELETED" if apply else "WOULD DELETE"
    width = max(len(r.table) for r in results) if results else 16
    lines = [
        f"{'TABLE':<{width}}  {'MATCHED':>10}  {header_action:>15}",
        "-" * (width + 30),
    ]
    total_matched = 0
    total_deleted = 0
    for r in results:
        lines.append(
            f"{r.table:<{width}}  {r.rows_matched:>10}  "
            f"{r.rows_deleted:>15}"
        )
        total_matched += r.rows_matched
        total_deleted += r.rows_deleted
    lines.append("-" * (width + 30))
    lines.append(
        f"{'TOTAL':<{width}}  {total_matched:>10}  "
        f"{total_deleted:>15}"
    )
    return "\n".join(lines)


def _redis_hint(property_id: str) -> str:
    """Operator hint for clearing the conversation-memory Redis keys."""
    return (
        f"redis-cli --tls -h bookly.redis.cache.windows.net -p 6380 "
        f"-a $REDIS_PASSWORD -n 1 "
        f"--scan --pattern 'conv:{property_id}:*' | "
        f"xargs -r redis-cli --tls -h bookly.redis.cache.windows.net "
        f"-p 6380 -a $REDIS_PASSWORD -n 1 DEL"
    )


def _qdrant_hint(property_id: str) -> str:
    """Operator hint for purging Qdrant memory points by metadata."""
    return (
        "curl -X POST $QDRANT_URL/collections/memory_facts/"
        "points/delete -H 'api-key: $QDRANT_API_KEY' "
        "-H 'Content-Type: application/json' "
        '-d \'{"filter":{"must":[{"key":"property_id",'
        f'"match":{{"value":"{property_id}"}}}}]}}\''
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns the shell exit code."""
    parser = argparse.ArgumentParser(
        description=(
            "Wipe every per-property row Brain Engine carries in "
            "Postgres for the named property.  Dry-run by default."
        ),
    )
    parser.add_argument(
        "--property-id",
        required=True,
        help="The property_id to purge (e.g. 323133).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually delete rows.  Without this flag the script is "
            "read-only and prints the counts that would be deleted."
        ),
    )
    args = parser.parse_args(argv)

    results = asyncio.run(
        cleanup_property(
            property_id=args.property_id,
            apply=args.apply,
        )
    )
    print(_format_report(results, apply=args.apply))
    print()
    print(
        "Redis conv:{pid}:* and Qdrant memory points are NOT "
        "purged by this script.  Run these manually if needed:"
    )
    print()
    print("Redis keys for the property:")
    print(f"  {_redis_hint(args.property_id)}")
    print()
    print("Qdrant memory points for the property:")
    print(f"  {_qdrant_hint(args.property_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
