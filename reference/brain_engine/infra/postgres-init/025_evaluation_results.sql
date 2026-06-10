-- 025_evaluation_results.sql
--
-- Golden Cases evaluation: per-run summaries and per-case judge verdicts.
--
-- Wired by `brain_engine/evaluation/golden_cases_runner.py` and
-- `brain_engine/continual_learning/nightly_consolidator.py` step 8.
--
-- Gated at runtime by env flag `BRAIN_GOLDEN_CASES_ENABLED` (default off);
-- migration is safe to apply even before the flag is flipped — the
-- tables stay empty until the first nightly run with the flag on.
--
-- UUID generation uses `uuid_generate_v4()` (provided by `uuid-ossp`,
-- enabled in `000_extensions.sql`); `gen_random_uuid()` is intentionally
-- avoided because `pgcrypto` is not enabled in this cluster.

CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    sample_size         INT  NOT NULL,
    pm_match_rate       DOUBLE PRECISION,
    hallucination_rate  DOUBLE PRECISION,
    avg_score           DOUBLE PRECISION,
    failed_cases        INT  NOT NULL DEFAULT 0,
    duration_seconds    DOUBLE PRECISION,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_evaluation_runs_started
    ON evaluation_runs (started_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_verdicts (
    verdict_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id        UUID NOT NULL REFERENCES evaluation_runs(run_id)
                       ON DELETE CASCADE,
    case_id       UUID NOT NULL,
    score         DOUBLE PRECISION NOT NULL,
    passed        BOOLEAN NOT NULL,
    value         TEXT NOT NULL DEFAULT 'N',
    criteria      TEXT NOT NULL DEFAULT '',
    reasoning     TEXT NOT NULL DEFAULT '',
    judge_model   TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evaluation_verdicts_run
    ON evaluation_verdicts (run_id);

CREATE INDEX IF NOT EXISTS idx_evaluation_verdicts_case
    ON evaluation_verdicts (case_id);

CREATE INDEX IF NOT EXISTS idx_evaluation_verdicts_score
    ON evaluation_verdicts (score, passed);
