"""
Jules Workflow — Loop-Schutz-Gates.
7 Schichten Defense-in-Depth, siehe Design-Doc §6.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .jules_state import JulesReviewRow

ALLOWED_TRIGGERS = frozenset({
    "pull_request:opened",
    "pull_request:synchronize",
    "pull_request:ready_for_review",
})

BLOCKED_TRIGGERS = frozenset({
    "issue_comment:created",
    "issue_comment:edited",
    "pull_request:edited",
    "pull_request:labeled",
    "pull_request:unlabeled",
    "pull_request_review:submitted",
    "pull_request_review:edited",
    "pull_request_review_comment:created",
})


@dataclass
class ReviewDecision:
    proceed: bool
    reason: str
    row: Optional[JulesReviewRow] = None

    @classmethod
    def skip(cls, reason: str) -> "ReviewDecision":
        return cls(proceed=False, reason=reason)

    @classmethod
    def advance(cls, row: JulesReviewRow) -> "ReviewDecision":
        return cls(proceed=True, reason="proceed", row=row)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gate_trigger_whitelist(event_type: str) -> Optional[str]:
    if event_type in BLOCKED_TRIGGERS:
        return "blocked_trigger"
    if event_type not in ALLOWED_TRIGGERS:
        return "unknown_trigger"
    return None


def gate_iteration_cap(row: JulesReviewRow, max_iterations: int = 5) -> Optional[str]:
    if row.iteration_count >= max_iterations:
        return "max_iterations"
    return None


def gate_time_cap(row: JulesReviewRow, max_hours: int = 2) -> Optional[str]:
    if row.created_at < now_utc() - timedelta(hours=max_hours):
        return "timeout_per_pr"
    return None


def gate_cooldown(row: JulesReviewRow, cooldown_seconds: int = 300) -> Optional[str]:
    if row.last_review_at is None:
        return None
    elapsed = (now_utc() - row.last_review_at).total_seconds()
    if elapsed < cooldown_seconds:
        return "cooldown"
    return None


async def check_circuit_breaker(
    redis_client, repo: str, threshold: int = 20, ttl_seconds: int = 3600
) -> tuple:
    """Schicht 5. Returns (is_open: bool, current_count: int)."""
    key = f"jules:circuit:{repo}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, ttl_seconds)
    return (count > threshold, int(count))
