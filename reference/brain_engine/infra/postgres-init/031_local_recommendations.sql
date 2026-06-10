-- ---------------------------------------------------------
-- R5 — owner-curated local recommendations column.
--
-- Adds a ``local_recommendations`` JSONB column to
-- ``owner_flexibility_profiles`` so the conversation pipeline
-- can quote owner-vetted nearby places (restaurants, cafés,
-- pharmacies, transit stops) instead of deferring to PM or
-- hallucinating from the LLM's pre-training data.
--
-- Schema shape — JSONB array of objects with the four fields
-- produced by :class:`brain_engine.owner_profile.models.LocalRecommendation`:
--   [
--     {
--       "category": "restaurant",
--       "name":     "La Casa",
--       "distance": "500m",
--       "notes":    "kid-friendly, open until midnight"
--     },
--     ...
--   ]
--
-- The column defaults to an empty array so the migration is a
-- no-op for every existing row.  ``infra/postgres-init/`` runs
-- only on first Postgres pod boot (see project_cd_migration_gap
-- memory) — for existing dev / prod databases this DDL must be
-- applied manually:
--
--   psql "$DATABASE_URL" \
--        -f infra/postgres-init/031_local_recommendations.sql
-- ---------------------------------------------------------

ALTER TABLE owner_flexibility_profiles
    ADD COLUMN IF NOT EXISTS local_recommendations JSONB
        NOT NULL DEFAULT '[]'::jsonb;
