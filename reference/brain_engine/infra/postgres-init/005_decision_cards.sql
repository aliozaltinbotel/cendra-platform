-- ---------------------------------------------------------
-- DECISION_CARDS — proposed cards surfaced to the V2 UI.
-- One row per StoredCard; status transitions are in-place
-- updates so the card id remains stable across lifecycle.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS decision_cards (
    card_id         TEXT        PRIMARY KEY,
    property_id     TEXT        NOT NULL,
    workflow        TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',
    payload         JSONB       NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    resolution_note TEXT,
    CHECK (status IN ('pending','confirmed','dismissed','expired'))
);

-- Hot path: UI asks for cards per property, newest first.
CREATE INDEX IF NOT EXISTS idx_decision_cards_property_created
    ON decision_cards (property_id, created_at DESC);

-- Partial index for the active-queue read (the most frequent
-- V2 UI call) so the planner avoids scanning resolved rows.
CREATE INDEX IF NOT EXISTS idx_decision_cards_pending
    ON decision_cards (property_id, created_at DESC)
    WHERE status = 'pending';
