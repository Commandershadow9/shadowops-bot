-- Multi-Agent Review Pipeline — Queue + Outcome Tracking
-- Siehe docs/plans/2026-04-14-multi-agent-review-design.md §7, §9

-- Queue für Jules-Session-Starts (nur POST /sessions)
CREATE TABLE IF NOT EXISTS agent_task_queue (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,                     -- 'jules_suggestion'|'scan_agent'|'manual'
    priority        INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 4),
    payload         JSONB NOT NULL,                    -- {repo, prompt, title, ...}
    project         TEXT,
    scheduled_for   TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_as     TEXT,                              -- jules session_id nach Release
    failure_reason  TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','released','failed','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_queue_next
    ON agent_task_queue(scheduled_for ASC, priority ASC)
    WHERE status = 'queued';

-- Outcome-Tracking für Auto-Merges
CREATE TABLE IF NOT EXISTS auto_merge_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    agent_type      TEXT NOT NULL,
    project         TEXT NOT NULL,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    rule_matched    TEXT NOT NULL,
    merged_at       TIMESTAMPTZ NOT NULL,
    -- 24h-Check (nachträglich befüllt)
    reverted                   BOOLEAN NOT NULL DEFAULT false,
    reverted_at                TIMESTAMPTZ,
    ci_passed_after_merge      BOOLEAN,
    deployed_without_incident  BOOLEAN,
    follow_up_fix_needed       BOOLEAN NOT NULL DEFAULT false,
    checked_at                 TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ame_agent_rule ON auto_merge_outcomes(agent_type, rule_matched);
CREATE INDEX IF NOT EXISTS idx_ame_pending_check
    ON auto_merge_outcomes(merged_at)
    WHERE checked_at IS NULL;

-- Bestehende jules_pr_reviews Tabelle um agent_type erweitern (additive Migration)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'jules_pr_reviews' AND column_name = 'agent_type'
    ) THEN
        ALTER TABLE jules_pr_reviews ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'jules';
        CREATE INDEX idx_jpr_agent_type ON jules_pr_reviews(agent_type);
    END IF;
END $$;
