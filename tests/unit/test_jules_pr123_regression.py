"""REGRESSION TEST für PR #123 — 31 comments in 90min → 0 reviews im neuen System."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from src.integrations.github_integration.jules_gates import (
    gate_trigger_whitelist,
    check_circuit_breaker,
)
from src.integrations.github_integration.jules_comment import is_bot_comment, COMMENT_MARKER
from src.integrations.github_integration.jules_state import JulesReviewRow


def _row(**kw):
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1, repo="ZERODOX", pr_number=123, issue_number=122,
        finding_id=None, status="reviewing", last_reviewed_sha=None,
        iteration_count=0, last_review_at=None, lock_acquired_at=now,
        lock_owner="w", review_comment_id=None, last_review_json=None,
        last_blockers=None, tokens_consumed=0, created_at=now, updated_at=now,
        closed_at=None, human_override=False,
    )
    defaults.update(kw)
    return JulesReviewRow(**defaults)


class _Cfg:
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600

    enabled = True
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2


@pytest.mark.asyncio
async def test_pr123_31_issue_comments_zero_reviews():
    """31 issue_comment events → all blocked by trigger whitelist."""
    from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin

    class H(JulesWorkflowMixin):
        pass

    h = H()
    h.jules_state = MagicMock(process_id="t")
    h.jules_state.try_claim_review = AsyncMock(return_value=None)
    h.redis = fakeredis.aioredis.FakeRedis()
    h.config = MagicMock()
    h.config.jules_workflow = _Cfg()
    h.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
    h.bot = MagicMock()

    for _ in range(31):
        d = await h.should_review("ZERODOX", 123, "any", "issue_comment:created")
        assert not d.proceed
        assert d.reason == "blocked_trigger"

    h.jules_state.try_claim_review.assert_not_called()


def test_pr123_bot_comment_detected():
    """Bot's own reviews must be detected by marker."""
    assert is_bot_comment(f"{COMMENT_MARKER} — Iteration 1\n\nstuff") is True
    assert is_bot_comment("Acknowledged. Thank you.") is False
    assert is_bot_comment("Looks good, merging.") is False


def test_pr123_all_comment_triggers_blocked():
    """Every issue_comment variant must be blocked."""
    assert gate_trigger_whitelist("issue_comment:created") == "blocked_trigger"
    assert gate_trigger_whitelist("issue_comment:edited") == "blocked_trigger"
    assert gate_trigger_whitelist("pull_request_review:submitted") == "blocked_trigger"
    assert gate_trigger_whitelist("pull_request_review_comment:created") == "blocked_trigger"


@pytest.mark.asyncio
async def test_pr123_circuit_breaker_stops_runaway():
    """Even if gates fail, circuit breaker stops after 20 reviews/h."""
    redis = fakeredis.aioredis.FakeRedis()
    for _ in range(20):
        await check_circuit_breaker(redis, "ZERODOX", threshold=20)
    is_open, count = await check_circuit_breaker(redis, "ZERODOX", threshold=20)
    assert is_open is True
    assert count >= 21
