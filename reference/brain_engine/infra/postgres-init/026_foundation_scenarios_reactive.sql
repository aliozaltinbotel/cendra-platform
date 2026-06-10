-- Reactive foundation scenario catalog (FL-01 — Foundation Layer Sprint 1).
--
-- Materialises the 469 hospitality scenarios curated in
--   ``Cendra_Brain_Engine_Hospitality_Scenario_Pattern_Learning_Foundation.md``
-- so Brain Engine code paths can join against the full schema (Risk
-- Level, Memory Type routing, Should-AI flags, What Not to Learn,
-- Pattern to Learn, …) without re-parsing the markdown on every
-- request.  The parser
--   ``brain_engine.patterns.foundation_registry``
-- produces :class:`FoundationScenario` rows; the store wrapper
--   ``brain_engine.patterns.foundation_catalog_store``
-- upserts them into this table.
--
-- This table is *the curated sector catalog* — not to be confused
-- with ``scenario_foundation`` (migration 024) which stores
-- *per-property learned feature importance* on top of the catalog.
--
-- Schema notes:
--   * ``scenario_id`` is the deterministic slug from
--     ``foundation_registry._build_id`` (e.g. ``s4_209_guest_reports_gas_smell``).
--     Stable across re-parses as long as stage number, scenario
--     index, and title prefix do not change.
--   * ``stage_number`` is constrained to the 9-stage ladder defined
--     by Section 3 *Scenario Counts* of the foundation document.
--   * ``risk_level`` is held as ``TEXT`` rather than ``ENUM`` so the
--     catalog can evolve (new risk classes, localisation) without a
--     schema migration; the consuming code parses it through
--     ``RiskLevel`` ``StrEnum`` validation.
--   * ``payload`` carries the full 14-field projection as JSONB.
--     Keeping it in one column avoids a 14-way schema migration each
--     time a new sub-section is added to the source markdown.  Query
--     paths that need a specific field reach in via ``payload->>'…'``;
--     a btree index on ``risk_level`` covers the hottest filter and
--     the JSONB GIN index covers everything else.
--   * ``doc_hash`` is the SHA-256 of the parsed markdown.  The
--     upsert path skips work when the stored hash matches the
--     current document hash, so pod startup costs O(1) when nothing
--     changed.
--   * ``parsed_at`` records the last time the row was (re-)written.
--     The freshness signal lives on the row, not on a separate
--     metadata table — one table per concern keeps invariants
--     local.
--
-- Backfill is deferred to the application: at pod boot the
-- foundation_catalog_store reads the shipped markdown, computes the
-- hash, and upserts when needed.  Tests can drive the same code
-- against the in-memory store.

BEGIN;

CREATE TABLE IF NOT EXISTS foundation_scenarios_reactive (
    scenario_id TEXT PRIMARY KEY,
    stage_number SMALLINT NOT NULL
        CHECK (stage_number BETWEEN 1 AND 9),
    stage_label TEXT NOT NULL,
    title TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    doc_hash TEXT NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_foundation_reactive_stage
    ON foundation_scenarios_reactive (stage_number, scenario_id);

CREATE INDEX IF NOT EXISTS idx_foundation_reactive_risk
    ON foundation_scenarios_reactive (risk_level)
    WHERE risk_level <> '';

CREATE INDEX IF NOT EXISTS idx_foundation_reactive_payload
    ON foundation_scenarios_reactive USING GIN (payload jsonb_path_ops);

COMMIT;
