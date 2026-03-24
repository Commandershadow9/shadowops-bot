"""
SecurityDB — Unified async Database Layer (asyncpg)

Ersetzt: KnowledgeBase (psycopg2, sync) + AnalystDB (asyncpg, async)
Nutzt: security_analyst DB auf Port 5433

Neue Tabellen: remediation_status, phase_executions, fix_attempts_v2
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger('shadowops.security_db')


class SecurityDB:
    """Unified Security Database — ein asyncpg-Pool fuer alles"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        self.pool = await asyncpg.create_pool(self.dsn, min_size=2, max_size=5)
        await self._ensure_schema()
        logger.info("SecurityDB initialisiert (asyncpg)")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _ensure_schema(self):
        """Erstellt alle Tabellen (IF NOT EXISTS), laesst bestehende intakt.

        Umfasst sowohl die Legacy-Tabellen (vom alten Analyst, gebraucht vom
        SecurityScanAgent) als auch die neuen v6-Tabellen (Reactive/Proactive Mode).
        """
        async with self.pool.acquire() as conn:
            # ── Legacy-Tabellen (SecurityScanAgent) ──────────────────
            await conn.execute("""
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
                CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

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
                CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category);

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
                CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
                CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
                CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(affected_project);

                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id SERIAL PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    examples JSONB DEFAULT '[]',
                    times_seen INT DEFAULT 1,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS health_snapshots (
                    id SERIAL PRIMARY KEY,
                    taken_at TIMESTAMPTZ DEFAULT NOW(),
                    session_id INT REFERENCES sessions(id),
                    services JSONB NOT NULL,
                    docker_containers JSONB NOT NULL,
                    system_resources JSONB NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fix_attempts (
                    id SERIAL PRIMARY KEY,
                    finding_id INT REFERENCES findings(id) ON DELETE CASCADE,
                    session_id INT REFERENCES sessions(id) ON DELETE SET NULL,
                    approach TEXT NOT NULL,
                    commands_used TEXT[],
                    result TEXT NOT NULL CHECK(result IN ('success', 'failure', 'partial')),
                    side_effects TEXT,
                    error_message TEXT,
                    execution_time_s INT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    verified_at TIMESTAMPTZ,
                    still_valid BOOLEAN
                );
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_finding ON fix_attempts(finding_id);
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_created ON fix_attempts(created_at DESC);

                CREATE TABLE IF NOT EXISTS fix_verifications (
                    id SERIAL PRIMARY KEY,
                    fix_attempt_id INT REFERENCES fix_attempts(id) ON DELETE CASCADE,
                    session_id INT REFERENCES sessions(id) ON DELETE SET NULL,
                    still_valid BOOLEAN NOT NULL,
                    check_method TEXT,
                    regression_details TEXT,
                    checked_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_fix_verifications_attempt ON fix_verifications(fix_attempt_id);

                CREATE TABLE IF NOT EXISTS finding_quality (
                    id SERIAL PRIMARY KEY,
                    finding_id INT UNIQUE REFERENCES findings(id) ON DELETE CASCADE,
                    is_actionable BOOLEAN,
                    is_false_positive BOOLEAN DEFAULT FALSE,
                    false_positive_reason TEXT,
                    discovery_method TEXT,
                    confidence_score FLOAT DEFAULT 0.5,
                    assessed_at TIMESTAMPTZ DEFAULT NOW(),
                    assessed_by TEXT DEFAULT 'analyst'
                );

                CREATE TABLE IF NOT EXISTS scan_coverage (
                    id SERIAL PRIMARY KEY,
                    session_id INT REFERENCES sessions(id) ON DELETE CASCADE,
                    area TEXT NOT NULL,
                    checked BOOLEAN DEFAULT TRUE,
                    depth TEXT DEFAULT 'basic' CHECK(depth IN ('basic', 'deep', 'skipped')),
                    notes TEXT,
                    checked_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_scan_coverage_area ON scan_coverage(area, checked_at DESC);
                CREATE INDEX IF NOT EXISTS idx_scan_coverage_session ON scan_coverage(session_id);

                CREATE TABLE IF NOT EXISTS ip_reputation (
                    id SERIAL PRIMARY KEY,
                    ip_address INET NOT NULL UNIQUE,
                    total_bans INT DEFAULT 0,
                    threat_score INT DEFAULT 0,
                    permanent_blocked BOOLEAN DEFAULT FALSE,
                    first_seen TIMESTAMPTZ DEFAULT NOW(),
                    last_seen TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # ── Neue v6-Tabellen (Reactive/Proactive Mode) ──────────
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fix_attempts_v2 (
                    id SERIAL PRIMARY KEY,
                    event_source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_signature TEXT NOT NULL,
                    phase_type TEXT NOT NULL DEFAULT 'fix',
                    approach TEXT,
                    commands JSONB DEFAULT '[]',
                    result TEXT NOT NULL CHECK(result IN (
                        'success', 'failed', 'partial', 'no_op', 'skipped_duplicate'
                    )),
                    error_message TEXT,
                    duration_ms INTEGER DEFAULT 0,
                    was_fast_path BOOLEAN DEFAULT FALSE,
                    engine_mode TEXT DEFAULT 'reactive',
                    batch_id TEXT,
                    finding_id INTEGER,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_sig ON fix_attempts_v2(event_signature);
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_result ON fix_attempts_v2(result);
                CREATE INDEX IF NOT EXISTS idx_fix_attempts_v2_created ON fix_attempts_v2(created_at);

                CREATE TABLE IF NOT EXISTS remediation_status (
                    id SERIAL PRIMARY KEY,
                    event_signature TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    handler TEXT NOT NULL CHECK(handler IN ('reactive', 'proactive', 'deep_scan')),
                    status TEXT NOT NULL DEFAULT 'in_progress' CHECK(status IN ('in_progress', 'completed', 'failed', 'released')),
                    claimed_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    UNIQUE(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_remediation_status_sig ON remediation_status(event_signature, status);

                CREATE TABLE IF NOT EXISTS phase_executions (
                    id SERIAL PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    phase_type TEXT NOT NULL,
                    phase_name TEXT NOT NULL,
                    events_processed INTEGER DEFAULT 0,
                    result TEXT NOT NULL CHECK(result IN ('success', 'failed', 'skipped', 'no_op')),
                    duration_ms INTEGER DEFAULT 0,
                    details JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_phase_exec_batch ON phase_executions(batch_id);
                CREATE INDEX IF NOT EXISTS idx_phase_exec_type ON phase_executions(phase_type);
            """)

    # ── Fix-Attempts ──────────────────────────────────────────────

    async def record_fix_attempt(self, event_source: str, event_type: str, event_signature: str,
                                 phase_type: str, approach: str, commands: List[str], result: str,
                                 duration_ms: int = 0, error_message: str = None,
                                 was_fast_path: bool = False, engine_mode: str = 'reactive',
                                 batch_id: str = None, finding_id: int = None,
                                 metadata: Dict = None) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO fix_attempts_v2 (event_source, event_type, event_signature, phase_type,
                approach, commands, result, duration_ms, error_message, was_fast_path,
                engine_mode, batch_id, finding_id, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            RETURNING id
        """, event_source, event_type, event_signature, phase_type,
            approach, json.dumps(commands), result, duration_ms, error_message,
            was_fast_path, engine_mode, batch_id, finding_id,
            json.dumps(metadata or {}, default=str))
        return row['id']

    async def get_fix_history(self, event_signature: str, days: int = 30, limit: int = 10) -> List[Dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self.pool.fetch("""
            SELECT id, approach, result, phase_type, duration_ms, error_message, was_fast_path, created_at
            FROM fix_attempts_v2 WHERE event_signature = $1 AND created_at > $2
            ORDER BY created_at DESC LIMIT $3
        """, event_signature, cutoff, limit)
        return [dict(r) for r in rows]

    async def get_success_rate(self, event_signature: str, days: int = 30) -> float:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        row = await self.pool.fetchrow("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE result = 'success') as successes,
                   COUNT(*) FILTER (WHERE result = 'no_op') as no_ops
            FROM fix_attempts_v2 WHERE event_signature = $1 AND created_at > $2
        """, event_signature, cutoff)
        total = row['total']
        if total == 0:
            return 0.5
        return (row['successes'] + row['no_ops']) / total

    # ── Remediation Status (Cross-Mode Lock) ─────────────────────

    async def claim_event(self, event_id: str, handler: str) -> bool:
        try:
            event_sig = event_id.rsplit('_', 1)[0] if '_' in event_id else event_id
            row = await self.pool.fetchrow("""
                INSERT INTO remediation_status (event_signature, event_id, handler)
                VALUES ($1, $2, $3)
                ON CONFLICT (event_id) DO UPDATE
                SET handler = $3, status = 'in_progress', claimed_at = NOW()
                WHERE remediation_status.status IN ('failed', 'released')
                RETURNING id
            """, event_sig, event_id, handler)
            return row is not None
        except Exception:
            return False

    async def release_event(self, event_id: str, status: str = 'completed') -> None:
        await self.pool.execute("""
            UPDATE remediation_status SET status = $2, completed_at = NOW() WHERE event_id = $1
        """, event_id, status)

    async def is_event_claimed(self, event_id: str) -> Optional[str]:
        row = await self.pool.fetchrow("""
            SELECT handler FROM remediation_status WHERE event_id = $1 AND status = 'in_progress'
        """, event_id)
        return row['handler'] if row else None

    # ── Phase Executions ─────────────────────────────────────────

    async def record_phase_execution(self, batch_id: str, phase_type: str, phase_name: str,
                                     events_processed: int, result: str, duration_ms: int = 0,
                                     details: Dict = None) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO phase_executions (batch_id, phase_type, phase_name, events_processed, result, duration_ms, details)
            VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
        """, batch_id, phase_type, phase_name, events_processed, result, duration_ms,
            json.dumps(details or {}, default=str))
        return row['id']

    async def get_phase_stats(self, days: int = 30) -> Dict[str, Any]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self.pool.fetch("""
            SELECT phase_type, COUNT(*) as total,
                   COUNT(*) FILTER (WHERE result = 'success') as successes,
                   COUNT(*) FILTER (WHERE result = 'no_op') as no_ops,
                   AVG(duration_ms) as avg_duration
            FROM phase_executions WHERE created_at > $1 GROUP BY phase_type
        """, cutoff)
        return {r['phase_type']: dict(r) for r in rows}

    # ── Strategy Stats (existierende Tabelle) ────────────────────

    async def update_strategy_stats(self, strategy_name: str, event_type: str,
                                    success: bool, phase_type: str = 'fix') -> None:
        await self.pool.execute("""
            INSERT INTO orchestrator_strategies (strategy_name, event_type, approach, success_rate, times_used, times_succeeded, last_used_at)
            VALUES ($1, $2, $3, $4, 1, $5, NOW())
            ON CONFLICT (strategy_name, event_type) DO UPDATE SET
                times_used = orchestrator_strategies.times_used + 1,
                times_succeeded = orchestrator_strategies.times_succeeded + $5::int,
                success_rate = (orchestrator_strategies.times_succeeded + $5::int)::real / (orchestrator_strategies.times_used + 1)::real,
                last_used_at = NOW()
        """, strategy_name, event_type, phase_type, 1.0 if success else 0.0, 1 if success else 0)

    # ── Bestehende Analyst-Tabellen (Wrapper) ────────────────────

    async def get_open_findings_count(self) -> int:
        row = await self.pool.fetchrow("SELECT COUNT(*) as cnt FROM findings WHERE status = 'open'")
        return row['cnt'] if row else 0

    async def store_knowledge(self, category: str, subject: str, content: str,
                              confidence: float = 0.5) -> int:
        row = await self.pool.fetchrow("""
            INSERT INTO knowledge (category, subject, content, confidence, last_verified)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (category, subject) DO UPDATE SET content = $3, confidence = $4, updated_at = NOW(), last_verified = NOW()
            RETURNING id
        """, category, subject, content, confidence)
        return row['id']

    async def get_knowledge(self, category: str, min_confidence: float = 0.2) -> List[Dict]:
        rows = await self.pool.fetch("""
            SELECT subject, content, confidence, last_verified FROM knowledge
            WHERE category = $1 AND confidence >= $2 ORDER BY confidence DESC
        """, category, min_confidence)
        return [dict(r) for r in rows]
