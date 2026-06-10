-- ---------------------------------------------------------
-- UNANSWERED_THREADS — one sandbox row per unanswered guest
-- conversation; ON CONFLICT (conversation_id) DO UPDATE
-- keeps the SQL path identical for first capture and the
-- re-harvest after the PM has reviewed the candidate.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS unanswered_threads (
    conversation_id      TEXT        NOT NULL PRIMARY KEY,
    property_id          TEXT        NOT NULL,
    last_guest_message   TEXT        NOT NULL,
    last_guest_sent_at   TIMESTAMPTZ NOT NULL,
    example_reply        TEXT        NOT NULL,
    generated_by         TEXT        NOT NULL,
    language             TEXT        NOT NULL DEFAULT '',
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Comma-separated rule names from
    -- ``brain_engine.sandbox.review_heuristics.classify_review_need``.
    -- Empty string means the heuristic check found nothing
    -- suspicious; the column stays empty for template-generated
    -- rows and rows produced before the heuristic was wired.
    needs_review_reason  TEXT        NOT NULL DEFAULT ''
);

-- Listing path orders by ``last_guest_sent_at DESC`` per property
-- so the UI surfaces the freshest unanswered thread first.
CREATE INDEX IF NOT EXISTS idx_unanswered_threads_by_property
    ON unanswered_threads (property_id, last_guest_sent_at DESC);
