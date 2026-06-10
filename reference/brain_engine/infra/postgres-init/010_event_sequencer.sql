-- ---------------------------------------------------------
-- EVENT_SEQUENCER — last applied (sequence, event_id) per
-- (topic, entity_id).  Read+write hot-path is a single
-- INSERT ... ON CONFLICT DO UPDATE WHERE; one row per subject.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_sequencer (
    topic           TEXT        NOT NULL,
    entity_id       TEXT        NOT NULL,
    last_sequence   BIGINT      NOT NULL,
    last_event_id   TEXT        NOT NULL,
    last_applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (topic, entity_id)
);

-- "Which subjects have been quiet for a while?" — used by the
-- DLQ-watch alert and by the cascade-consumer health probe.
CREATE INDEX IF NOT EXISTS idx_event_sequencer_recency
    ON event_sequencer (last_applied_at DESC);
