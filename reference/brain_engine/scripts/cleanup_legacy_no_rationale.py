"""One-off cleanup for PatternRules created before the rationale fix.

Background
----------
Before commit ``f85950c`` the V2 onboarding bootstrap pipeline
(``brain_engine.patterns.pattern_miner``) skipped three Mümin
fixes that had only landed in the API extractor path
(:class:`PatternExtractor`): deterministic ``pattern_id``,
subsumption merge, and the ``rationale`` field.  As a result,
property scopes that bootstrapped through the miner accumulated
duplicate rules with random ``pattern_id`` and an empty
``rationale``.

The miner fix prevents *new* duplicates; this script removes
the *existing* legacy rows from the live registry by setting
``active = false``.  Selection criteria are deliberately narrow:

* ``rationale == ""`` — the unambiguous fingerprint of a
  pre-fix bootstrap rule (every post-fix rule carries a
  non-empty one-line explanation).
* ``active is True`` — already-inactive rows are left alone
  so re-runs are idempotent.
* ``--scope-id`` is **required** so the operator opts in
  per-property; there is no scope-wide sweep.

Usage
-----
Dry-run (default — no writes)::

    python scripts/cleanup_legacy_no_rationale.py --scope-id 323133

Apply once you have eyeballed the dry-run output::

    python scripts/cleanup_legacy_no_rationale.py \\
        --scope-id 323133 --apply

Environment
-----------
The script reuses :func:`build_pattern_rule_store` so it inherits
the same env vars the API server uses
(``PATTERN_RULE_STORE_BACKEND=postgres`` plus
``DECISION_CASE_STORE_DATABASE_URL`` or ``DATABASE_URL``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Make the project root importable when invoked directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import structlog  # noqa: E402  -- after sys.path bootstrap

from brain_engine.patterns.models import PatternRule  # noqa: E402
from brain_engine.patterns.wiring import (  # noqa: E402
    build_pattern_rule_store,
)

logger = structlog.get_logger(__name__).bind(
    component="cleanup_legacy_no_rationale",
)

_EXIT_OK: Final[int] = 0
_EXIT_ERROR: Final[int] = 1


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Aggregate outcome of one script invocation."""

    inspected: int
    selected: int
    deactivated: int
    already_inactive: int
    by_scenario: dict[str, int]


def _select_legacy(rules: list[PatternRule]) -> list[PatternRule]:
    """Return rules with an empty ``rationale`` field.

    Empty rationale uniquely identifies pre-``f85950c`` bootstrap
    rows: every post-fix rule emits a one-line explanation built
    by ``_build_rationale``.  No further filter is needed — the
    field is the fingerprint.
    """
    return [r for r in rules if not r.rationale]


async def _run(*, apply: bool, scope_id: str) -> CleanupReport:
    """Execute the cleanup pass and return an aggregate report."""
    store, close = await build_pattern_rule_store()
    try:
        rules = await store.get_active_rules(scope_id=scope_id)
        selected = _select_legacy(rules)

        by_scenario: dict[str, int] = {}
        deactivated = 0
        already_inactive = 0

        for rule in selected:
            label = rule.scenario.value
            by_scenario[label] = by_scenario.get(label, 0) + 1

            if not apply:
                print(
                    f"[DRY-RUN] would deactivate "
                    f"{rule.pattern_id[:12]} "
                    f"scenario={label} "
                    f"action={rule.action.action_type.value} "
                    f"support={rule.support_count} "
                    f"created={rule.created_at.isoformat()[:19]}",
                )
                continue

            changed = await store.deactivate(rule.pattern_id)
            if changed:
                deactivated += 1
                print(
                    f"[OK] deactivated "
                    f"{rule.pattern_id[:12]} "
                    f"scenario={label} "
                    f"action={rule.action.action_type.value}",
                )
            else:
                already_inactive += 1
                print(
                    f"[SKIP] {rule.pattern_id[:12]} already inactive "
                    f"(scenario={label})",
                )

        return CleanupReport(
            inspected=len(rules),
            selected=len(selected),
            deactivated=deactivated,
            already_inactive=already_inactive,
            by_scenario=by_scenario,
        )
    finally:
        await close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.  Default mode is dry-run."""
    parser = argparse.ArgumentParser(
        description=(
            "Deactivate PatternRules with empty rationale (the "
            "fingerprint of pre-f85950c bootstrap rows).  Default "
            "is dry-run; --scope-id is required."
        ),
    )
    parser.add_argument(
        "--scope-id",
        type=str,
        required=True,
        help=(
            "Property id (or other scope id) to sweep.  Required "
            "so the operator opts in per scope; there is no "
            "scope-wide sweep."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually call rule_store.deactivate(...).  Without "
            "this flag the script only prints what it would do."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns a Unix exit code."""
    args = _parse_args(argv)
    try:
        report = asyncio.run(
            _run(apply=args.apply, scope_id=args.scope_id),
        )
    except (ValueError, OSError, ConnectionError) as exc:
        logger.error(
            "cleanup_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return _EXIT_ERROR

    mode = "apply" if args.apply else "dry-run"
    print()
    print(
        f"[SUMMARY] mode={mode} scope_id={args.scope_id} "
        f"inspected={report.inspected} "
        f"selected={report.selected} "
        f"deactivated={report.deactivated} "
        f"already_inactive={report.already_inactive}",
    )
    if report.by_scenario:
        for scenario_label, count in sorted(report.by_scenario.items()):
            print(f"  scenario={scenario_label}: {count}")
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
