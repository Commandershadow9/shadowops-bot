-- Security Analyst Datenbank-Schema
-- Ausfuehren: docker exec -i guildscout-postgres psql -U security_analyst -d security_analyst < src/integrations/analyst/db_setup.sql

-- Sessions muessen zuerst erstellt werden (wird von findings referenziert)
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    trigger_type TEXT NOT NULL,
    topics_investigated TEXT[],
    findings_count INT DEFAULT 0,
    auto_fixes_count INT DEFAULT 0,
    issues_created INT DEFAULT 0,
    tokens_used INT DEFAULT 0,
    model_used TEXT,
    ai_summary TEXT,
    status TEXT DEFAULT 'running'
);

-- Akkumuliertes Wissen ueber den Server
CREATE TABLE IF NOT EXISTS knowledge (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    last_verified TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(category, subject)
);

-- Gefundene Security-Findings
CREATE TABLE IF NOT EXISTS findings (
    id SERIAL PRIMARY KEY,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    affected_project TEXT,
    affected_files TEXT[],
    status TEXT DEFAULT 'open',
    fix_type TEXT,
    github_issue_url TEXT,
    auto_fix_details TEXT,
    rollback_command TEXT,
    found_at TIMESTAMPTZ DEFAULT NOW(),
    fixed_at TIMESTAMPTZ,
    session_id INT REFERENCES sessions(id)
);

-- Gelernte Patterns (waechst ueber Zeit)
CREATE TABLE IF NOT EXISTS learned_patterns (
    id SERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,
    description TEXT NOT NULL,
    examples JSONB DEFAULT '[]',
    times_seen INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Service Health Snapshots (fuer Rollback-Entscheidungen)
CREATE TABLE IF NOT EXISTS health_snapshots (
    id SERIAL PRIMARY KEY,
    taken_at TIMESTAMPTZ DEFAULT NOW(),
    session_id INT REFERENCES sessions(id),
    services JSONB NOT NULL,
    docker_containers JSONB NOT NULL,
    system_resources JSONB NOT NULL
);

-- Indizes
CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(affected_project);
CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
