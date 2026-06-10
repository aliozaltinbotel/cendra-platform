-- FL-03 — bridge column linking decisions / rules to the foundation
-- catalog (foundation_scenarios_reactive, migration 026).
--
-- Decision cases and pattern rules already carry a coarse
-- ``scenario`` enum value (``Scenario`` in
-- ``brain_engine/patterns/models.py``).  That enum will not be
-- widened to the 469 hospitality scenarios from the foundation
-- markdown — the catalog table is the source of truth.  Instead
-- we add an optional ``foundation_scenario_id`` TEXT column to
-- both tables; the column stores the deterministic slug emitted
-- by ``foundation_registry._build_id`` (e.g.
-- ``"s4_209_guest_reports_gas_smell"``).
--
-- Schema notes:
--   * ``foundation_scenario_id`` is nullable so existing rows stay
--     valid after the migration runs without a backfill.  The
--     orchestrator (FL-16, Sprint 2) populates it on new cases
--     once it consults ``scenario_matcher``.
--   * No foreign-key constraint is enforced — the column may
--     reference a slug that is later renamed in the markdown, and
--     we want a slug churn in the catalog to surface as an
--     observability event rather than as a hard insert failure.
--     ``foundation_catalog_store.get`` returning ``None`` is the
--     designed signal.
--   * Indexes cover the two queries the bridge unlocks:
--       1. "every case for foundation scenario X" — FL-12 origin
--          trail.
--       2. "every rule mined from foundation scenario X" — FL-13
--          update-feedback fan-out and FL-15 LLM iterative
--          questioning.
--     Both indexes are partial (``WHERE foundation_scenario_id IS
--     NOT NULL``) so they do not pay the storage cost on legacy
--     rows that pre-date FL-16.

BEGIN;

ALTER TABLE decision_cases
    ADD COLUMN IF NOT EXISTS foundation_scenario_id TEXT;

ALTER TABLE pattern_rules
    ADD COLUMN IF NOT EXISTS foundation_scenario_id TEXT;

CREATE INDEX IF NOT EXISTS idx_decision_cases_foundation_scenario
    ON decision_cases (foundation_scenario_id)
    WHERE foundation_scenario_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pattern_rules_foundation_scenario
    ON pattern_rules (foundation_scenario_id)
    WHERE foundation_scenario_id IS NOT NULL;

COMMIT;
