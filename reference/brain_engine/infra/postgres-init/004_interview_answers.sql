-- ---------------------------------------------------------
-- INTERVIEW_ANSWERS — PM responses to the proactive
-- interview catalog.  One row per (property_id, qid);
-- ON CONFLICT upsert keeps the SQL path identical for
-- the first capture and every re-answer.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS interview_answers (
    property_id TEXT NOT NULL,
    qid TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    answered_by TEXT NOT NULL DEFAULT 'pm',
    answered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (property_id, qid)
);

-- Coverage report fans out by property_id; stage filtering
-- happens in Python against the catalog, so a single index
-- is enough.
CREATE INDEX IF NOT EXISTS idx_ia_by_property
    ON interview_answers (property_id, answered_at DESC);
