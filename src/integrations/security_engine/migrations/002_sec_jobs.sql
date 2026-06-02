-- 002_sec_jobs.sql — Security-Agent-Team Job-Lifecycle (#290 P1)
-- HINWEIS: operativ angewandt via SecurityDB._ensure_schema(); diese Datei ist
-- Doku-Paritaet zu 001 (es gibt keinen Migrations-Runner).
CREATE TABLE IF NOT EXISTS sec_jobs (
    job_id        UUID PRIMARY KEY,
    worker_type   TEXT NOT NULL,
    project       TEXT NOT NULL,
    trigger       TEXT NOT NULL DEFAULT 'manual',
    status        TEXT NOT NULL DEFAULT 'queued',
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    result        JSONB,
    tokens_used   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sec_jobs_status  ON sec_jobs (status);
CREATE INDEX IF NOT EXISTS idx_sec_jobs_project ON sec_jobs (project, created_at DESC);
