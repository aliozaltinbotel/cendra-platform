-- FL-13 — foundation update feedback backlog.
--
-- Closes Ali's Turkish requirement #2 (foundation feedback loop).
-- When a foundation scenario keeps mis-fitting a customer (the PM
-- overrides the engine's foundation-derived response three or
-- more times in comparable cases) the detector at
-- ``brain_engine.patterns.foundation_update.detect_foundation_drift``
-- emits a :class:`FoundationUpdateCandidate` and persists it
-- here.  The candidate is *never* applied to the foundation
-- markdown automatically — a human reviewer triages it via the
-- future ``/foundation/updates`` admin surface.
--
-- Schema notes:
--   * The natural key ``(foundation_scenario_id, scope, scope_id)``
--     guarantees that re-running the detector on the same drift
--     refreshes the existing row in place instead of producing
--     duplicates.  ``candidate_id`` is a generated UUID hex used
--     by the admin surface for direct addressing.
--   * ``source_case_ids`` is a TEXT[] so the reviewer can follow
--     the link back to the raw :class:`DecisionCase` evidence
--     without a JSONB cast.
--   * ``severity`` is held as TEXT (not ENUM) so a future tier
--     refinement (e.g. adding ``"critical"``) does not need a
--     schema migration.
--   * ``updated_at`` lives next to ``created_at`` so the upsert
--     path can stamp it from ``ON CONFLICT DO UPDATE`` without a
--     trigger; old rows that came in via the very first INSERT
--     mirror ``created_at`` until the first re-upsert.

BEGIN;

CREATE TABLE IF NOT EXISTS foundation_update_candidates (
    id BIGSERIAL PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    foundation_scenario_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    override_count INTEGER NOT NULL CHECK (override_count > 0),
    severity TEXT NOT NULL,
    deviation_evidence TEXT NOT NULL DEFAULT '',
    proposed_change TEXT NOT NULL DEFAULT '',
    source_case_ids TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (foundation_scenario_id, scope, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_foundation_update_scenario
    ON foundation_update_candidates (foundation_scenario_id);

CREATE INDEX IF NOT EXISTS idx_foundation_update_scope
    ON foundation_update_candidates (scope, scope_id);

CREATE INDEX IF NOT EXISTS idx_foundation_update_severity_created
    ON foundation_update_candidates (severity, created_at DESC);

COMMIT;
