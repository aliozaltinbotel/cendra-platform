-- ---------------------------------------------------------
-- Branch 4: capture the §10 priority-chain verdict on every
-- DecisionCase so pattern miners can attribute outcomes to
-- the tier that fired (manual / blocker / safety / learned /
-- preference / ask).  Existing rows backfill to ``{}`` —
-- legacy cases stay learnable, just without tier attribution.
-- ---------------------------------------------------------
ALTER TABLE decision_cases
    ADD COLUMN IF NOT EXISTS orchestrator_verdict JSONB
    NOT NULL DEFAULT '{}'::jsonb;
