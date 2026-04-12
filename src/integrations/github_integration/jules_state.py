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
