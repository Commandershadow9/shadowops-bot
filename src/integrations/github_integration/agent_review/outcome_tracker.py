"""OutcomeTracker — verfolgt Auto-Merge-Outcomes fuer 24h-Revert-Detection.

Schreibt nach Auto-Merge einen Eintrag in `auto_merge_outcomes` mit
checked_at=NULL. Ein stuendlicher Loop ruft `check_pending_outcomes()`
und fuer jeden Merge, der > 24h alt ist, wird geprueft:
- Ist der Merge-Commit zurueckgerevertet worden? → reverted=true
- Ist CI nach dem Merge gelaufen? (aus gh api)
- Wurde die geaenderte Datei innerhalb 24h erneut geaendert? → follow_up_fix_needed

Learning-Output: Rules mit hoher Revert-Rate werden in der Daily-Digest
angezeigt, damit wir die merge_policy() Adapter anpassen koennen.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class AutoMergeOutcome:
    id: int
    agent_type: str
    project: str
    repo: str
    pr_number: int
    rule_matched: str
    merged_at: datetime
    reverted: bool
    checked_at: Optional[datetime]


class OutcomeTracker:
    """asyncpg-Layer fuer auto_merge_outcomes."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def record_auto_merge(
        self,
        *,
        agent_type: str,
        project: str,
        repo: str,
        pr_number: int,
        rule_matched: str,
    ) -> int:
        """Speichert einen frischen Auto-Merge. checked_at bleibt NULL."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO auto_merge_outcomes
                       (agent_type, project, repo, pr_number, rule_matched, merged_at)
                   VALUES ($1, $2, $3, $4, $5, now())
                   RETURNING id""",
                agent_type, project, repo, pr_number, rule_matched,
            )
            return int(row["id"])

    async def get_pending_outcomes(self, min_age_hours: int = 24) -> List[AutoMergeOutcome]:
        """Holt Auto-Merges die mindestens `min_age_hours` alt sind und noch nicht geprueft."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, agent_type, project, repo, pr_number, rule_matched,
                          merged_at, reverted, checked_at
                   FROM auto_merge_outcomes
                   WHERE checked_at IS NULL
                     AND merged_at < now() - ($1::int || ' hours')::interval
                   ORDER BY merged_at ASC""",
                min_age_hours,
            )
        return [AutoMergeOutcome(
            id=r["id"], agent_type=r["agent_type"], project=r["project"],
            repo=r["repo"], pr_number=r["pr_number"], rule_matched=r["rule_matched"],
            merged_at=r["merged_at"], reverted=r["reverted"], checked_at=r["checked_at"],
        ) for r in rows]

    async def mark_checked(
        self,
        outcome_id: int,
        *,
        reverted: bool = False,
        reverted_at: Optional[datetime] = None,
        ci_passed: Optional[bool] = None,
        deployed_ok: Optional[bool] = None,
        follow_up_fix_needed: bool = False,
    ) -> None:
        """Setzt checked_at=now() und speichert die 24h-Check Ergebnisse."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE auto_merge_outcomes
                   SET reverted = $2,
                       reverted_at = $3,
                       ci_passed_after_merge = $4,
                       deployed_without_incident = $5,
                       follow_up_fix_needed = $6,
                       checked_at = now()
                   WHERE id = $1""",
                outcome_id, reverted, reverted_at, ci_passed, deployed_ok,
                follow_up_fix_needed,
            )

    async def revert_rate_by_rule(
        self, *, agent_type: Optional[str] = None, days: int = 30,
    ) -> List[dict]:
        """Revert-Statistik pro rule_matched fuer Learning-Feedback.

        Returns [{"rule_matched": "...", "total": N, "reverted": M, "rate_pct": X}, ...]
        """
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            where = "merged_at > now() - ($1::int || ' days')::interval AND checked_at IS NOT NULL"
            params = [days]
            if agent_type is not None:
                where += " AND agent_type = $2"
                params.append(agent_type)

            rows = await conn.fetch(
                f"""SELECT rule_matched,
                           COUNT(*)::int AS total,
                           SUM(CASE WHEN reverted THEN 1 ELSE 0 END)::int AS reverted
                    FROM auto_merge_outcomes
                    WHERE {where}
                    GROUP BY rule_matched
                    ORDER BY reverted DESC, total DESC""",
                *params,
            )
        return [
            {
                "rule_matched": r["rule_matched"],
                "total": r["total"],
                "reverted": r["reverted"],
                "rate_pct": (100.0 * r["reverted"] / r["total"]) if r["total"] else 0.0,
            }
            for r in rows
        ]

    async def last_24h_summary(self) -> dict:
        """Zaehlt Auto-Merges + Reverts der letzten 24h (fuer Daily-Digest)."""
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                     COUNT(*)::int AS total,
                     SUM(CASE WHEN reverted THEN 1 ELSE 0 END)::int AS reverted,
                     SUM(CASE WHEN checked_at IS NULL THEN 1 ELSE 0 END)::int AS pending
                   FROM auto_merge_outcomes
                   WHERE merged_at > now() - interval '24 hours'"""
            )
        return {
            "total": int(row["total"] or 0),
            "reverted": int(row["reverted"] or 0),
            "pending": int(row["pending"] or 0),
        }
