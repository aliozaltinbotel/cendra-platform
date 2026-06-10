-- Per-property feature importance store (Sprint I — foundation analysis).
--
-- Replaces the hand-curated whitelist in
-- ``brain_engine/patterns/scenario_features.py`` (Sprint H stop-gap)
-- with a data-driven mapping that the nightly foundation analyser
-- learns from 6 months of DecisionCase history.  When the
-- per-property mapping is fresh enough (controlled by
-- ``BRAIN_FOUNDATION_REFRESH_DAYS``) the ConditionSynthesizer reads
-- it instead of the hardcoded fallback so the surface adapts to
-- per-property reality without code changes.
--
-- Schema notes:
--   * (property_id, scenario, feature_name) is the natural key — a
--     property has exactly one importance score per (scenario,
--     feature) at any given time, refreshed in place.
--   * sample_count is recorded so downstream code can demote
--     foundations that were trained on too little data and fall
--     back to the global default surface.
--   * computed_at is the freshness signal the consumer compares
--     against ``now() - INTERVAL 'BRAIN_FOUNDATION_REFRESH_DAYS d'``.
--   * The CHECK gate on importance keeps the column interpretable
--     as a fraction of total impurity reduction (sklearn's
--     feature_importances_ already normalises to [0, 1]).
--
-- Backfill is intentionally empty: nothing is "learned" until the
-- nightly job runs at least once for a property, at which point the
-- analyser inserts the full importance landscape it discovered.

BEGIN;

CREATE TABLE IF NOT EXISTS scenario_foundation (
    id BIGSERIAL PRIMARY KEY,
    property_id TEXT NOT NULL,
    scenario TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    importance REAL NOT NULL
        CHECK (importance >= 0.0 AND importance <= 1.0),
    sample_count INT NOT NULL CHECK (sample_count >= 0),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (property_id, scenario, feature_name)
);

CREATE INDEX IF NOT EXISTS idx_scenario_foundation_lookup
    ON scenario_foundation (property_id, scenario, importance DESC);

CREATE INDEX IF NOT EXISTS idx_scenario_foundation_freshness
    ON scenario_foundation (property_id, scenario, computed_at);

COMMIT;
