-- Jules SecOps Workflow — PR-Review State
-- Lebt in der security_analyst Datenbank
-- Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md Abschnitt 7.1

CREATE TABLE IF NOT EXISTS jules_pr_reviews (
    id              BIGSERIAL PRIMARY KEY,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    issue_number    INTEGER,
    finding_id      INTEGER,

    status          TEXT NOT NULL CHECK (status IN (
                      'pending','reviewing','approved','revision_requested',
                      'escalated','merged','abandoned')),

    last_reviewed_sha  TEXT,
    iteration_count    INTEGER NOT NULL DEFAULT 0,
    last_review_at     TIMESTAMPTZ,
    lock_acquired_at   TIMESTAMPTZ,
    lock_owner         TEXT,

    review_comment_id  BIGINT,
    last_review_json   JSONB,
    last_blockers      JSONB,
    tokens_consumed    INTEGER NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    human_override  BOOLEAN NOT NULL DEFAULT false,

    UNIQUE (repo, pr_number)
);

CREATE INDEX IF NOT EXISTS idx_jules_status ON jules_pr_reviews(status)
    WHERE status NOT IN ('merged','abandoned');

CREATE INDEX IF NOT EXISTS idx_jules_finding ON jules_pr_reviews(finding_id);

-- FK wird nachträglich hinzugefügt wenn findings-Tabelle existiert (soft coupling)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'findings') THEN
        BEGIN
            ALTER TABLE jules_pr_reviews
                ADD CONSTRAINT fk_jules_finding
                FOREIGN KEY (finding_id) REFERENCES findings(id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END;
    END IF;
END $$;

-- View für Metriken
CREATE OR REPLACE VIEW jules_daily_stats AS
SELECT
    date_trunc('day', created_at) AS day,
    repo,
    COUNT(*) FILTER (WHERE status = 'approved')           AS approved,
    COUNT(*) FILTER (WHERE status = 'revision_requested') AS revisions,
    COUNT(*) FILTER (WHERE status = 'escalated')          AS escalated,
    COUNT(*) FILTER (WHERE status = 'merged')             AS merged,
    AVG(iteration_count)                                  AS avg_iterations,
    SUM(tokens_consumed)                                  AS total_tokens
FROM jules_pr_reviews
GROUP BY 1, 2
ORDER BY 1 DESC, 2;
