-- ---------------------------------------------------------
-- WORKFLOW_AUTONOMY — per-(property, workflow) state machine.
-- One row per (property_id, workflow); upserted by the engine
-- on every metrics update or PM-forced transition.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflow_autonomy (
    property_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'observe',
    sample_size INTEGER NOT NULL DEFAULT 0,
    success_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    override_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    incidents INTEGER NOT NULL DEFAULT 0,
    mean_latency_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    hold_seconds INTEGER NOT NULL DEFAULT 60,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by TEXT NOT NULL DEFAULT 'system',
    reason TEXT NOT NULL DEFAULT 'initialized',
    PRIMARY KEY (property_id, workflow)
);

-- Trust Meter endpoint reads every workflow for a property at
-- once; the partial-free index keeps the lookup ~O(workflows).
CREATE INDEX IF NOT EXISTS idx_wf_autonomy_by_property
    ON workflow_autonomy (property_id);

-- "Which properties hit AUTOPILOT this week?" / dashboard fan-out.
CREATE INDEX IF NOT EXISTS idx_wf_autonomy_state
    ON workflow_autonomy (state, changed_at DESC);
