-- ---------------------------------------------------------
-- BLOCKERS — preconditions that hold sensitive actions.
-- Replaces the in-memory reference store; survives restarts.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS blockers (
    blocker_id TEXT PRIMARY KEY,
    blocker_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    property_id TEXT NOT NULL,
    reservation_id TEXT,
    description TEXT NOT NULL DEFAULT '',
    blocks_actions TEXT[] DEFAULT '{}',
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

-- Hot path: BlockerEngine.check_blockers / has_hard_blocker.
-- Partial index keeps it small and fast even on a long table.
CREATE INDEX IF NOT EXISTS idx_blockers_active_by_property
    ON blockers (property_id)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_blockers_active_by_reservation
    ON blockers (property_id, reservation_id)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_blockers_created
    ON blockers (created_at DESC);
