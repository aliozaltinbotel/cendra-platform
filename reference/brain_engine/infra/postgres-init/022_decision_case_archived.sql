-- Soft-archive column on decision_cases (Sprint 4 — forgetting curve).
--
-- Adds ``archived_at`` so the nightly archiver can move stale cases
-- out of the *hot* working set without physically deleting any
-- row.  All queries that drive learning (``PatternMiner``,
-- ``PatternExtractor``) are amended to filter
-- ``WHERE archived_at IS NULL`` so an archived case stops feeding
-- the miner; audit / forensics queries can opt in to archived
-- rows when needed.
--
-- Why soft-archive instead of physical delete:
--   * Audit trail — historical reservations may need point-in-time
--     replay; deleting the source case kills the provenance edge
--     between PatternRule.source_case_ids and the underlying
--     evidence.
--   * Recoverable — if the archival heuristic mis-fires (ar
--     critical case archived too early) we just clear archived_at.
--   * Faster table — once archived rows are filtered out by
--     index, the working set shrinks → HNSW / GIN scans speed up
--     without partition surgery.
--
-- Backfill is intentionally NULL on every existing row: nothing is
-- archived until the operator runs the nightly job (or manual
-- script) at least once.

BEGIN;

ALTER TABLE decision_cases
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_dc_archived_at
    ON decision_cases (archived_at);

COMMIT;
