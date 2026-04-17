"""
LearningBridge — Bidirektionale agent_learning DB Integration

Liest Cross-Agent-Wissen und schreibt Security-Fix-Feedback zurück,
damit alle Agents voneinander lernen.

Getrennte DB-Verbindung zur agent_learning DB (nicht security_analyst DB).
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger('shadowops.learning_bridge')

def _get_agent_learning_dsn() -> str:
    """Agent-Learning DSN aus Config/Env laden."""
    from utils.config import get_config
    dsn = get_config().agent_learning_dsn
    if not dsn:
        raise RuntimeError("agent_learning DSN nicht konfiguriert (AGENT_LEARNING_DB_URL oder config.yaml)")
    return dsn


class LearningBridge:
    """Verbindet Security Engine mit dem Cross-Agent Learning System"""

    def __init__(self, dsn: str = None):
        self.dsn = dsn or _get_agent_learning_dsn()
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        """Verbindung zur agent_learning DB herstellen"""
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3)
            logger.info("LearningBridge verbunden mit agent_learning DB")
        except Exception as e:
            logger.warning(f"LearningBridge konnte nicht verbinden: {e}")
            self.pool = None

    async def close(self):
        if self.pool:
            await self.pool.close()

    @property
    def is_connected(self) -> bool:
        return self.pool is not None

    # ── LESEN: Cross-Agent Knowledge ──

    async def get_cross_agent_knowledge(self, category: str = 'security', limit: int = 20) -> List[Dict]:
        """Liest Wissen von anderen Agents (SEO, Feedback, etc.)"""
        if not self.pool:
            return []
        try:
            rows = await self.pool.fetch("""
                SELECT agent, category, subject, content, confidence, created_at
                FROM agent_knowledge
                WHERE category = $1 AND confidence > 0.3
                ORDER BY confidence DESC, created_at DESC
                LIMIT $2
            """, category, limit)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.debug(f"Cross-Agent Knowledge Abruf fehlgeschlagen: {e}")
            return []

    async def get_agent_quality_trends(self, agent: str = 'security_engine', days: int = 30) -> Dict[str, Any]:
        """Liest Quality-Trends für den Security Agent"""
        if not self.pool:
            return {'avg_score': 0.0, 'trend': 'unknown', 'sample_count': 0}
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            row = await self.pool.fetchrow("""
                SELECT AVG(combined_score) as avg_score,
                       COUNT(*) as sample_count
                FROM agent_quality_scores
                WHERE agent = $1 AND assessed_at > $2
            """, agent, cutoff)
            avg = float(row['avg_score']) if row and row['avg_score'] else 0.0
            count = row['sample_count'] if row else 0
            return {
                'avg_score': avg,
                'sample_count': count,
                'trend': 'improving' if avg > 0.7 else 'stable' if avg > 0.4 else 'declining',
            }
        except Exception as e:
            logger.debug(f"Quality-Trend Abruf fehlgeschlagen: {e}")
            return {'avg_score': 0.0, 'trend': 'unknown', 'sample_count': 0}

    # ── SCHREIBEN: Security Fix Feedback ──

    async def record_fix_feedback(self, project: str, fix_id: str,
                                   success: bool, fix_type: str = 'auto_fix',
                                   metadata: Dict = None) -> Optional[int]:
        """Schreibt Fix-Ergebnis als agent_feedback zurück"""
        if not self.pool:
            return None
        try:
            row = await self.pool.fetchrow("""
                INSERT INTO agent_feedback (
                    agent, project, reference_id, feedback_type,
                    score_delta, metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
                RETURNING id
            """,
                'security_engine', project, str(fix_id), fix_type,
                1 if success else -1,
                json.dumps(metadata or {}, default=str),
            )
            return row['id'] if row else None
        except Exception as e:
            logger.debug(f"Fix-Feedback Schreiben fehlgeschlagen: {e}")
            return None

    async def record_quality_score(self, project: str, reference_id: str,
                                    auto_score: float, feedback_score: float = None) -> Optional[int]:
        """Schreibt Quality-Score für einen Security-Fix"""
        if not self.pool:
            return None
        try:
            combined = auto_score
            if feedback_score is not None:
                combined = (auto_score * 0.6 + feedback_score * 0.4)
            row = await self.pool.fetchrow("""
                INSERT INTO agent_quality_scores (
                    agent, project, reference_id, auto_score,
                    feedback_score, combined_score, sample_count
                ) VALUES ($1, $2, $3, $4, $5, $6, 1)
                ON CONFLICT (agent, project, reference_id) DO UPDATE SET
                    auto_score = $4, feedback_score = $5,
                    combined_score = $6, assessed_at = NOW()
                RETURNING id
            """,
                'security_engine', project, reference_id,
                auto_score, feedback_score, combined,
            )
            return row['id'] if row else None
        except Exception as e:
            logger.debug(f"Quality-Score Schreiben fehlgeschlagen: {e}")
            return None

    async def share_knowledge(self, category: str, subject: str,
                               content: str, confidence: float = 0.5) -> Optional[int]:
        """Teilt Security-Wissen mit anderen Agents"""
        if not self.pool:
            return None
        try:
            row = await self.pool.fetchrow("""
                INSERT INTO agent_knowledge (
                    agent, category, subject, content, confidence, created_at
                ) VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (agent, category, subject) DO UPDATE SET
                    content = $4, confidence = $5, updated_at = NOW()
                RETURNING id
            """,
                'security_engine', category, subject, content, confidence,
            )
            return row['id'] if row else None
        except Exception as e:
            logger.debug(f"Knowledge-Sharing fehlgeschlagen: {e}")
            return None

    async def get_learning_summary(self) -> Dict[str, Any]:
        """Zusammenfassung des Learning-Status"""
        knowledge = await self.get_cross_agent_knowledge()
        quality = await self.get_agent_quality_trends()
        return {
            'cross_agent_knowledge_count': len(knowledge),
            'quality_trend': quality,
            'connected': self.is_connected,
        }

    # ── DEDUP-FEEDBACK ──

    async def record_dedup_decision(
        self, parent_id: int, new_title: str, project: Optional[str] = None
    ) -> None:
        """Schreibt eine Auto-Dedup-Entscheidung in agent_feedback."""
        if not self.pool:
            return
        try:
            await self.pool.execute(
                """INSERT INTO agent_feedback
                   (agent, project, reference_id, feedback_type, metadata)
                   VALUES ($1, $2, $3, $4, $5)""",
                "security-scan-agent",
                project or "infrastructure",
                str(parent_id),
                "auto_dedup_merge",
                json.dumps({"new_title": new_title[:200]}),
            )
        except Exception as e:
            logger.warning("record_dedup_decision failed: %s", e)

    async def record_manual_merge(
        self,
        parent_id: int,
        child_id: int,
        user_id: int,
        user_name: str,
        project: Optional[str] = None,
    ) -> None:
        """User hat manuell zwei Findings als Duplikat markiert (via /mark-duplicate)."""
        if not self.pool:
            return
        try:
            await self.pool.execute(
                """INSERT INTO agent_feedback
                   (agent, project, reference_id, feedback_type, user_id, user_name,
                    score_delta, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                "security-scan-agent",
                project or "infrastructure",
                str(parent_id),
                "manual_dedup_merge",
                user_id,
                user_name,
                1,  # positives Signal: Dedup war korrekt
                json.dumps({"child_id": child_id}),
            )
        except Exception as e:
            logger.warning("record_manual_merge failed: %s", e)
