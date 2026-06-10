-- FL-12 — provenance trail on decisions and rules.
--
-- Each :class:`DecisionCase` and :class:`PatternRule` now carries
-- an ``origin`` JSONB payload that records the foundation scenarios,
-- raw upstream events, and proactive signals that contributed to it.
-- This closes Ali's Turkish requirement #1: every learnt rule must
-- trace back to the foundation row(s) that birthed it.  The structure
-- mirrors :class:`brain_engine.patterns.models.PatternOrigin`:
--
--   {
--     "foundation_scenario_ids": ["s4_209_guest_reports_gas_smell", …],
--     "source_event_ids": ["msg_123", "pms_evt_456"],
--     "contributing_signal_ids": ["signal_789"]
--   }
--
-- All three keys are optional; legacy rows and rows pre-dating the
-- orchestrator (Sprint 2 onwards) keep the empty default ``'{}'::jsonb``
-- and round-trip through ``PatternOrigin.from_jsonable`` to the empty
-- value object.
--
-- Schema notes:
--   * NOT NULL with a default of ``'{}'::jsonb`` so a backfill is
--     unnecessary — legacy rows materialise the empty origin without
--     a separate UPDATE pass.
--   * GIN index over the payload covers the fan-out query "every case
--     / rule whose origin references foundation scenario X", which is
--     the workload the rule-origin API endpoint and the FL-13
--     update-feedback fan-out will run.  Partial indexes guard the
--     storage cost: only rows with a non-empty origin pay.
--
-- The migration is idempotent: re-running it on a schema that already
-- carries the column is a no-op thanks to ``ADD COLUMN IF NOT EXISTS``.

BEGIN;

ALTER TABLE decision_cases
    ADD COLUMN IF NOT EXISTS origin JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE pattern_rules
    ADD COLUMN IF NOT EXISTS origin JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_decision_cases_origin
    ON decision_cases USING GIN (origin jsonb_path_ops)
    WHERE origin <> '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_pattern_rules_origin
    ON pattern_rules USING GIN (origin jsonb_path_ops)
    WHERE origin <> '{}'::jsonb;

COMMIT;
