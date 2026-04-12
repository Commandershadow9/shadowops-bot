-- Jules Review Learning — Few-Shot Examples
-- Lebt in der agent_learning Datenbank (Port 5433, GuildScout Postgres)
-- Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md Abschnitt 7.2

CREATE TABLE IF NOT EXISTS jules_review_examples (
    id              BIGSERIAL PRIMARY KEY,
    project         TEXT NOT NULL,
    pr_ref          TEXT,
    diff_summary    TEXT NOT NULL,
    review_json     JSONB NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN (
                      'good_catch','false_positive','missed_issue','approved_clean')),
    user_feedback   TEXT,
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jrex_project_outcome
    ON jules_review_examples(project, outcome);

CREATE INDEX IF NOT EXISTS idx_jrex_weight
    ON jules_review_examples(project, weight DESC, created_at DESC);
