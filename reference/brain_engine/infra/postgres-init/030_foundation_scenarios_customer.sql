-- FL-14 — customer-facing foundation tier.
--
-- Closes Ali's Turkish requirement #3 (customer-facing foundation
-- as a second-tier reference).  Every rule a PM authors in the
-- ``rule_creation`` UI gains a second life as a foundation
-- reference for the orchestrator's matcher and the FL-15 LLM
-- iterative-questioning prompt.
--
-- Schema notes:
--   * The natural key ``(customer_id, scenario_id)`` scopes
--     entries per customer — one customer's rules never leak
--     into another's reasoning.
--   * ``payload`` mirrors the FL-01 core-foundation schema
--     (15 sub-section fields) so the orchestrator can fold core
--     and customer entries through the same matcher path.  Stored
--     as JSONB to dodge a 15-way schema migration whenever a new
--     foundation field lands.
--   * ``source_rule_id`` records the ``rule_creation`` workflow
--     id that birthed the scenario — provenance link used by the
--     future ``/foundation/customer/{id}/source`` admin API.
--   * Indexes cover the two access patterns: lookup by id pair
--     (orchestrator hot path) and "every scenario for customer X"
--     (UI listing).  No GIN index on payload yet — the matcher
--     embeds the trigger text via the existing ScenarioMatcher
--     index rather than reading payload directly.

BEGIN;

CREATE TABLE IF NOT EXISTS foundation_scenarios_customer (
    id BIGSERIAL PRIMARY KEY,
    customer_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    title TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_rule_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_foundation_customer_customer
    ON foundation_scenarios_customer (customer_id, scenario_id);

CREATE INDEX IF NOT EXISTS idx_foundation_customer_source_rule
    ON foundation_scenarios_customer (source_rule_id)
    WHERE source_rule_id <> '';

COMMIT;
