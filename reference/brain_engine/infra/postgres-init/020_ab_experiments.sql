-- ---------------------------------------------------------
-- AB_EXPERIMENTS — registry rows for every experiment the
-- engine has registered.  ``variants`` is a JSONB array of
-- ``{variant_id, weight, is_control}`` objects so adding a
-- new field (e.g. ``description``) does not need a schema
-- migration.  ``salt`` is what the deterministic traffic
-- splitter hashes against; ``alpha`` and ``min_trials_per_arm``
-- are the verdict thresholds; ``control_id`` is denormalised
-- so the verdict reader does not have to scan ``variants``.
--
-- ``status`` is one of: ``running``, ``stopped``, ``archived``.
-- ``ended_at`` is set when the runtime stops the experiment;
-- ``created_at`` is the original registration timestamp.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS ab_experiments (
    experiment_id        TEXT         PRIMARY KEY,
    name                 TEXT         NOT NULL DEFAULT '',
    hypothesis           TEXT         NOT NULL DEFAULT '',
    variants             JSONB        NOT NULL,
    salt                 TEXT         NOT NULL DEFAULT '',
    alpha                DOUBLE PRECISION NOT NULL DEFAULT 0.05,
    min_trials_per_arm   INTEGER      NOT NULL DEFAULT 50,
    control_id           TEXT         NOT NULL,
    status               TEXT         NOT NULL DEFAULT 'running',
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ended_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ab_experiments_status
    ON ab_experiments (status, created_at DESC);

-- ---------------------------------------------------------
-- AB_OUTCOMES — append-only ledger.  Every call to
-- ``ExperimentRegistry.record_outcome`` writes one row.  The
-- verdict pipeline re-aggregates per (experiment_id,
-- variant_id) at read time so out-of-order or replayed
-- outcomes converge to the same verdict without needing an
-- update path on a cached counter.
--
-- ``metadata`` carries optional context (e.g. subject id,
-- decision case id) so analysts can drill from a verdict
-- back to the originating decision.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS ab_outcomes (
    id              BIGSERIAL    PRIMARY KEY,
    experiment_id   TEXT         NOT NULL
        REFERENCES ab_experiments (experiment_id)
        ON DELETE CASCADE,
    variant_id      TEXT         NOT NULL,
    success         BOOLEAN      NOT NULL,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Verdict reads aggregate by (experiment_id, variant_id);
-- this index keeps that path on a bitmap-index scan rather
-- than a sequential scan once the ledger grows.
CREATE INDEX IF NOT EXISTS idx_ab_outcomes_experiment_variant
    ON ab_outcomes (experiment_id, variant_id);
