"""
Jules Workflow — State Management.

Asyncpg-Layer für security_analyst.jules_pr_reviews.
Atomic Lock-Claim, Stale-Lock-Recovery, CRUD.

Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §7.1.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class JulesReviewRow:
    id: int
    repo: str
    pr_number: int
    issue_number: Optional[int]
    finding_id: Optional[int]
    status: str
    last_reviewed_sha: Optional[str]
    iteration_count: int
    last_review_at: Optional[datetime]
    lock_acquired_at: Optional[datetime]
    lock_owner: Optional[str]
    review_comment_id: Optional[int]
    last_review_json: Optional[dict]
    last_blockers: Optional[list]
    tokens_consumed: int
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    human_override: bool

    @classmethod
    def from_record(cls, rec: asyncpg.Record) -> "JulesReviewRow":
        return cls(**dict(rec))


class JulesState:
    """Thin asyncpg wrapper um jules_pr_reviews."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """JSON/JSONB Codec registrieren, damit dict/list statt str zurückkommen."""
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await conn.set_type_codec(
            "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=4, command_timeout=10,
                init=self._init_connection,
            )
            logger.info("✅ JulesState connected to security_analyst DB")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def process_id(self) -> str:
        return f"shadowops-bot-pid-{os.getpid()}"

    # ── Task 3.2: Atomic Lock-Claim + SHA-Dedupe ──────────────

    async def try_claim_review(
        self, repo: str, pr_number: int, head_sha: str, lock_owner: str
    ) -> Optional[JulesReviewRow]:
        sql = """
            UPDATE jules_pr_reviews
            SET status = 'reviewing',
                lock_acquired_at = now(),
                lock_owner = $1,
                updated_at = now()
            WHERE repo = $2
              AND pr_number = $3
              AND status IN ('pending', 'revision_requested', 'approved')
              AND (last_reviewed_sha IS NULL OR last_reviewed_sha != $4)
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow(sql, lock_owner, repo, pr_number, head_sha)
            return JulesReviewRow.from_record(rec) if rec else None

    async def release_lock(self, row_id: int, new_status: str) -> None:
        valid = {"pending", "approved", "revision_requested", "escalated", "merged", "abandoned"}
        if new_status not in valid:
            raise ValueError(f"Ungültiger Status: {new_status}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET status=$1, lock_owner=NULL, lock_acquired_at=NULL, updated_at=now() WHERE id=$2",
                new_status, row_id,
            )

    async def mark_reviewed_sha(self, row_id: int, sha: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET last_reviewed_sha=$1, last_review_at=now(), iteration_count=iteration_count+1, updated_at=now() WHERE id=$2",
                sha, row_id,
            )

    # ── Task 3.3: Stale-Lock-Recovery ─────────────────────────

    async def recover_stale_locks(self, timeout_minutes: int = 10) -> int:
        sql = """
            UPDATE jules_pr_reviews
            SET status = 'revision_requested', lock_owner = NULL, lock_acquired_at = NULL, updated_at = now()
            WHERE status = 'reviewing' AND lock_acquired_at < now() - ($1 || ' minutes')::interval
            RETURNING id
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, str(timeout_minutes))
            count = len(rows)
            if count:
                logger.warning(f"🔓 Jules Stale-Lock-Recovery: {count} Lock(s) zurückgesetzt")
            return count

    # ── Task 3.4: CRUD-Helpers ────────────────────────────────

    async def ensure_pending(self, repo, pr_number, issue_number, finding_id) -> JulesReviewRow:
        sql = """
            INSERT INTO jules_pr_reviews (repo, pr_number, issue_number, finding_id, status)
            VALUES ($1, $2, $3, $4, 'pending')
            ON CONFLICT (repo, pr_number) DO UPDATE
              SET issue_number = COALESCE(jules_pr_reviews.issue_number, EXCLUDED.issue_number),
                  finding_id = COALESCE(jules_pr_reviews.finding_id, EXCLUDED.finding_id),
                  updated_at = now()
            RETURNING *
        """
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow(sql, repo, pr_number, issue_number, finding_id)
            return JulesReviewRow.from_record(rec)

    async def get(self, repo, pr_number) -> Optional[JulesReviewRow]:
        async with self._pool.acquire() as conn:
            rec = await conn.fetchrow("SELECT * FROM jules_pr_reviews WHERE repo=$1 AND pr_number=$2", repo, pr_number)
            return JulesReviewRow.from_record(rec) if rec else None

    async def update_comment_id(self, row_id, comment_id) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE jules_pr_reviews SET review_comment_id=$1, updated_at=now() WHERE id=$2", comment_id, row_id)

    async def store_review_result(self, row_id, review_json, blockers, tokens) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET last_review_json=$1, last_blockers=$2, tokens_consumed=tokens_consumed+$3, updated_at=now() WHERE id=$4",
                review_json, blockers, tokens, row_id,
            )

    async def mark_terminal(self, row_id, status) -> None:
        if status not in {"merged", "abandoned", "escalated"}:
            raise ValueError(f"mark_terminal nur für Terminal-States, nicht {status}")
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE jules_pr_reviews SET status=$1, closed_at=now(), lock_owner=NULL, lock_acquired_at=NULL, updated_at=now() WHERE id=$2",
                status, row_id,
            )

    async def fetch_health_stats(self) -> dict:
        async with self._pool.acquire() as conn:
            active = await conn.fetchval("SELECT COUNT(*) FROM jules_pr_reviews WHERE status='reviewing'")
            pending = await conn.fetchval("SELECT COUNT(*) FROM jules_pr_reviews WHERE status='pending'")
            escalated = await conn.fetchval("SELECT COUNT(*) FROM jules_pr_reviews WHERE status='escalated' AND closed_at > now() - interval '24 hours'")
            stats = await conn.fetchrow("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status='approved') AS approved,
                       COUNT(*) FILTER (WHERE status='revision_requested') AS revisions,
                       COUNT(*) FILTER (WHERE status='merged') AS merged,
                       COALESCE(SUM(tokens_consumed),0) AS tokens
                FROM jules_pr_reviews WHERE updated_at > now() - interval '24 hours'
            """)
            last = await conn.fetchval("SELECT MAX(last_review_at) FROM jules_pr_reviews")
        return {
            "active_reviews": int(active or 0), "pending_prs": int(pending or 0),
            "escalated_24h": int(escalated or 0),
            "stats_24h": {"total_reviews": int(stats["total"] or 0), "approved": int(stats["approved"] or 0),
                          "revisions": int(stats["revisions"] or 0), "merged": int(stats["merged"] or 0),
                          "tokens_consumed": int(stats["tokens"] or 0)},
            "last_review_at": last.isoformat() if last else None,
        }
