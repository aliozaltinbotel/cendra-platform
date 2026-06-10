"""One-off cleanup for PatternRules that should never have been stored.

Background
----------
Before commit ``<<this PR>>`` the V2 onboarding bootstrap pipeline
persisted every mined :class:`PatternRule` to ``pattern_rules``
without running :class:`PatternValidator`.  Mümin found that
``GET /patterns/rules?scenario=cancellation_request`` returned rules
even though ``POST /patterns/extract`` flagged them as
``valid: false`` ("Scenario … in NEVER_AUTO_LEARN blacklist").

The pipeline fix prevents *new* leaks; this script removes the
*existing* ones.  It is intentionally narrow: only rules whose
scenario sits in :data:`NEVER_AUTO_SCENARIOS` or whose action
``category`` / ``domain`` sits in :data:`NEVER_AUTO_LEARN` are
deactivated.  Rules that fail other validator checks (low support,
stale evidence, hidden-variable suspicion, …) are *not* touched —
they may still mature into legitimate auto-rules.

Usage
-----
Dry-run first (default — no writes)::

    python scripts/cleanup_blacklisted_rules.py

Apply the cleanup once you have eyeballed the dry-run output::

    python scripts/cleanup_blacklisted_rules.py --apply

Optional ``--scope-id <property_id>`` narrows the sweep to a single
property; omitted, the script scans every property.

Environment
-----------
The script reuses :func:`build_pattern_rule_store` so it inherits the
same env vars the API server uses
(``PATTERN_RULE_STORE_BACKEND=postgres`` plus
``DECISION_CASE_STORE_DATABASE_URL`` or ``DATABASE_URL``).  No
separate DSN is required.

The deactivation is idempotent: a second run will report zero
changes because :meth:`deactivate` only flips ``active=true → false``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Make the project root importable when this file is invoked directly
# (``python scripts/cleanup_blacklisted_rules.py``) without
# pre-setting ``PYTHONPATH``.  The repo is not pip-installed.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import structlog  # noqa: E402  -- after sys.path bootstrap

from brain_engine.patterns.models import PatternRule  # noqa: E402
from brain_engine.patterns.store import PatternRuleStore  # noqa: E402
from brain_engine.patterns.validator import (  # noqa: E402
    NEVER_AUTO_LEARN,
    NEVER_AUTO_SCENARIOS,
)
from brain_engine.patterns.wiring import (  # noqa: E402
    build_pattern_rule_store,
)

logger = structlog.get_logger(__name__).bind(
    component="cleanup_blacklisted_rules",
)

_EXIT_OK: Final[int] = 0
_EXIT_ERROR: Final[int] = 1


@dataclass(frozen=True, slots=True)
class CleanupVerdict:
    """Why a rule was selected for deactivation."""

    rule: PatternRule
    reason: str

    @property
    def short_id(self) -> str:
        """First eight characters of the pattern id, for log lines."""
        return self.rule.pattern_id[:8]


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Aggregate outcome of one script invocation."""

    inspected: int
    selected: int
    deactivated: int
    already_inactive: int
    by_scenario: dict[str, int]


def _select_blacklisted(rules: list[PatternRule]) -> list[CleanupVerdict]:
    """Filter ``rules`` down to the entries that violate the blacklist.

    Mirrors :meth:`PatternValidator._check_not_blacklisted` but uses
    the public module-level frozensets so the script stays in sync
    with the validator without depending on a private method.
    """
    selected: list[CleanupVerdict] = []
    for rule in rules:
        if rule.scenario in NEVER_AUTO_SCENARIOS:
            selected.append(
                CleanupVerdict(
                    rule=rule,
                    reason=(
                        f"scenario '{rule.scenario.value}' "
                        "in NEVER_AUTO_SCENARIOS"
                    ),
                ),
            )
            continue

        category = rule.action.params.get("category", "")
        if category in NEVER_AUTO_LEARN:
            selected.append(
                CleanupVerdict(
                    rule=rule,
                    reason=(
                        f"action.category '{category}' "
                        "in NEVER_AUTO_LEARN"
                    ),
                ),
            )
            continue

        domain = rule.action.params.get("domain", "")
        if domain in NEVER_AUTO_LEARN:
            selected.append(
                CleanupVerdict(
                    rule=rule,
                    reason=(
                        f"action.domain '{domain}' "
                        "in NEVER_AUTO_LEARN"
                    ),
                ),
            )
    return selected


async def _fetch_candidates(
    store: PatternRuleStore,
    *,
    scope_id: str | None,
) -> list[PatternRule]:
    """Pull every active rule the script needs to inspect.

    Two passes are required:

    * One per scenario in :data:`NEVER_AUTO_SCENARIOS` — these are
      cheap because the SQL filter narrows the result set server-side.
    * One unrestricted pull to catch rules whose action ``category``
      or ``domain`` falls in :data:`NEVER_AUTO_LEARN` (no SQL filter
      can match those without leaking implementation detail into the
      query layer).

    The two pulls can overlap, so results are de-duplicated by
    ``pattern_id`` before being returned to the caller.
    """
    seen: dict[str, PatternRule] = {}

    for scenario in NEVER_AUTO_SCENARIOS:
        scoped = await store.get_active_rules(
            scenario=scenario,
            scope_id=scope_id,
        )
        for rule in scoped:
            seen[rule.pattern_id] = rule

    full = await store.get_active_rules(scope_id=scope_id)
    for rule in full:
        seen.setdefault(rule.pattern_id, rule)

    return list(seen.values())


async def _run(*, apply: bool, scope_id: str | None) -> CleanupReport:
    """Execute the cleanup pass and return an aggregate report."""
    store, close = await build_pattern_rule_store()
    try:
        rules = await _fetch_candidates(store, scope_id=scope_id)
        verdicts = _select_blacklisted(rules)

        by_scenario: dict[str, int] = {}
        deactivated = 0
        already_inactive = 0

        for verdict in verdicts:
            scenario_label = verdict.rule.scenario.value
            by_scenario[scenario_label] = (
                by_scenario.get(scenario_label, 0) + 1
            )

            if not apply:
                print(
                    f"[DRY-RUN] would deactivate "
                    f"{verdict.short_id} "
                    f"scenario={scenario_label} "
                    f"scope={verdict.rule.scope.value}:"
                    f"{verdict.rule.scope_id} "
                    f"reason={verdict.reason}",
                )
                continue

            changed = await store.deactivate(verdict.rule.pattern_id)
            if changed:
                deactivated += 1
                print(
                    f"[OK] deactivated "
                    f"{verdict.short_id} "
                    f"scenario={scenario_label} "
                    f"scope={verdict.rule.scope.value}:"
                    f"{verdict.rule.scope_id} "
                    f"reason={verdict.reason}",
                )
            else:
                already_inactive += 1
                print(
                    f"[SKIP] {verdict.short_id} already inactive "
                    f"(scenario={scenario_label})",
                )

        return CleanupReport(
            inspected=len(rules),
            selected=len(verdicts),
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
            "Deactivate PatternRules whose scenario or action sits "
            "in the NEVER_AUTO_LEARN / NEVER_AUTO_SCENARIOS "
            "blacklist.  Default is dry-run."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually call rule_store.deactivate(...).  Without this "
            "flag the script only prints what it *would* do."
        ),
    )
    parser.add_argument(
        "--scope-id",
        type=str,
        default=None,
        help=(
            "Restrict the sweep to a single scope id (e.g. a "
            "property id like 323133).  Omit to scan every scope."
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

    mode = "APPLY" if args.apply else "DRY-RUN"
    print()
    print(f"=== {mode} summary ===")
    print(f"inspected      : {report.inspected}")
    print(f"selected       : {report.selected}")
    if args.apply:
        print(f"deactivated    : {report.deactivated}")
        print(f"already_inactive: {report.already_inactive}")
    if report.by_scenario:
        print("by scenario:")
        for scenario, count in sorted(report.by_scenario.items()):
            print(f"  {scenario}: {count}")
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
