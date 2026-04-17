"""
Jules Learning — Kontext-Loader aus agent_learning DB.

Stellt few-shot-Examples und Projekt-Knowledge für den Review-Prompt bereit.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class JulesLearning:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, command_timeout=10
            )
            logger.info("JulesLearning connected to agent_learning DB")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def fetch_few_shot_examples(
        self, project: str, limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Holt die besten Few-Shot-Beispiele für ein Projekt, sortiert nach Gewicht."""
        sql = """
            SELECT project, pr_ref, diff_summary, review_json, outcome, weight, created_at
            FROM jules_review_examples
            WHERE project = $1
            ORDER BY weight DESC, created_at DESC
            LIMIT $2
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, project, limit)
        out = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("review_json"), str):
                try:
                    d["review_json"] = json.loads(d["review_json"])
                except Exception:
                    d["review_json"] = {}
            out.append(d)
        return out

    async def fetch_project_knowledge(
        self, project: str, limit: int = 10
    ) -> List[str]:
        """Holt Projekt-Knowledge aus agent_knowledge für den Jules-Reviewer.

        Die agent_knowledge-Tabelle hat Spalten: agent, category, subject, content, confidence.
        Wir filtern auf agent='jules_reviewer' und subject=<project>.
        """
        sql = """
            SELECT content FROM agent_knowledge
            WHERE agent = 'jules_reviewer'
              AND subject = $1
            ORDER BY confidence DESC NULLS LAST, updated_at DESC
            LIMIT $2
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, project, limit)
            return [r["content"] for r in rows if r["content"]]
        except asyncpg.UndefinedTableError:
            logger.warning("agent_knowledge-Tabelle fehlt — Learning-Context leer")
            return []
        except asyncpg.UndefinedColumnError as e:
            logger.warning(f"agent_knowledge Schema-Mismatch: {e}")
            return []
