"""One-shot DecisionCase archival CLI (Sprint 4 — forgetting curve).

Soft-archives cases whose ``created_at`` is older than the
configured retention horizon **and** that no active
:class:`PatternRule` references via ``source_case_ids``.  Pure
metadata flip on ``decision_cases.archived_at`` — no row is
deleted, audit trails stay intact, and the operator can clear
``archived_at`` to bring a row back into the working set.

Usage
-----
Dry-run preview (no writes — the default mode of the underlying
SQL statement still flips the timestamp, so this script *runs*
the archival; the ``--dry-run`` flag below is a higher-level
guard that prints the candidates instead of archiving them)::

    python scripts/archive_stale_cases.py

Apply the archival once you have eyeballed the dry-run output::

    python scripts/archive_stale_cases.py --apply

Options
-------
``--retention-days N`` (default 90)
    Cases newer than ``now() - N days`` are kept in the working
    set.  Match to your operational freshness window.

``--batch-limit N`` (default 1000)
    Cap on candidates fetched per invocation.  Re-run the script
    until the candidate count drops to zero to drain a backlog.

Environment
-----------
The script reuses :func:`build_decision_case_store` so it
inherits the same env vars the API server uses
(``DECISION_CASE_STORE_BACKEND=postgres`` plus
``DECISION_CASE_STORE_DATABASE_URL`` or ``DATABASE_URL``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Final

# Make the project root importable when invoked directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import structlog  # noqa: E402  -- after sys.path bootstrap

from brain_engine.patterns.case_archiver import (  # noqa: E402
    DEFAULT_BATCH_LIMIT,
    DEFAULT_RETENTION_DAYS,
    CaseArchiver,
)
from brain_engine.patterns.wiring import (  # noqa: E402
    build_decision_case_store,
)

logger = structlog.get_logger(__name__).bind(
    component="archive_stale_cases",
)

_EXIT_OK: Final[int] = 0
_EXIT_ERROR: Final[int] = 1


async def _run(
    *,
    apply: bool,
    retention_days: int,
    batch_limit: int,
) -> int:
    """Execute one archival pass and return a Unix exit code."""
    store, close = await build_decision_case_store()
    try:
        archiver = CaseArchiver(
            store,
            retention_days=retention_days,
            batch_limit=batch_limit,
        )
        if not apply:
            from datetime import UTC, datetime, timedelta

            cutoff = datetime.now(UTC) - timedelta(days=retention_days)
            candidates = await store.select_archive_candidates(
                cutoff=cutoff, limit=batch_limit,
            )
            for case_id in candidates:
                print(f"[DRY-RUN] would archive {case_id}")
            print(
                f"\n[SUMMARY] mode=dry-run "
                f"retention_days={retention_days} "
                f"batch_limit={batch_limit} "
                f"candidates={len(candidates)} archived=0",
            )
            return _EXIT_OK

        report = await archiver.archive_stale_cases()
        print(
            f"[SUMMARY] mode=apply "
            f"retention_days={report.retention_days} "
            f"batch_limit={report.batch_limit} "
            f"candidates={report.candidates} "
            f"archived={report.archived} "
            f"cutoff={report.cutoff.isoformat()}",
        )
        return _EXIT_OK
    finally:
        await close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.  Default mode is dry-run."""
    parser = argparse.ArgumentParser(
        description=(
            "Soft-archive stale DecisionCase rows.  Default is "
            "dry-run; pass --apply to actually flip archived_at."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually call store.archive(...) for every "
            "candidate.  Without this flag the script only "
            "prints the candidates it would archive."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=(
            "Cases older than now() - N days are eligible "
            f"(default {DEFAULT_RETENTION_DAYS})."
        ),
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
        help=(
            "Maximum candidates fetched per invocation "
            f"(default {DEFAULT_BATCH_LIMIT})."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns a Unix exit code."""
    args = _parse_args(argv)
    try:
        return asyncio.run(
            _run(
                apply=args.apply,
                retention_days=args.retention_days,
                batch_limit=args.batch_limit,
            ),
        )
    except (ValueError, OSError, ConnectionError) as exc:
        logger.error(
            "archival_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
