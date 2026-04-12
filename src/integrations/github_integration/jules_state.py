"""
Jules Workflow — State Management.

Asyncpg-Layer für security_analyst.jules_pr_reviews.
Atomic Lock-Claim, Stale-Lock-Recovery, CRUD.

Siehe docs/plans/2026-04-11-jules-secops-workflow-design.md §7.1.
"""
from __future__ import annotations

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

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=4, command_timeout=10
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
