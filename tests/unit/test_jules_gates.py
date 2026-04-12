"""Tests fuer jules_gates.py — Loop-Schutz-Gates + Redis Circuit-Breaker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.integrations.github_integration.jules_gates import (
    ReviewDecision,
    check_circuit_breaker,
    gate_cooldown,
    gate_iteration_cap,
    gate_time_cap,
    gate_trigger_whitelist,
)
from src.integrations.github_integration.jules_state import JulesReviewRow


# ── Helper ────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(**overrides) -> JulesReviewRow:
    """Erstellt eine JulesReviewRow mit sinnvollen Defaults."""
    defaults = dict(
        id=1,
        repo="Commandershadow9/shadowops-bot",
        pr_number=42,
        issue_number=None,
        finding_id=None,
        status="pending",
        last_reviewed_sha=None,
        iteration_count=0,
        last_review_at=None,
        lock_acquired_at=None,
        lock_owner=None,
        review_comment_id=None,
        last_review_json=None,
        last_blockers=None,
        tokens_consumed=0,
        created_at=_now(),
        updated_at=_now(),
        closed_at=None,
        human_override=False,
    )
    defaults.update(overrides)
    return JulesReviewRow(**defaults)


# ── Trigger Whitelist ─────────────────────────────────────────

class TestGateTriggerWhitelist:

    def test_pr_opened_allowed(self):
        assert gate_trigger_whitelist("pull_request:opened") is None

    def test_pr_synchronize_allowed(self):
        assert gate_trigger_whitelist("pull_request:synchronize") is None

    def test_pr_ready_for_review_allowed(self):
        assert gate_trigger_whitelist("pull_request:ready_for_review") is None

    def test_issue_comment_blocked_regression_pr123(self):
        """Regression-Test fuer PR #123 — issue_comment DARF NICHT Jules triggern."""
        result = gate_trigger_whitelist("issue_comment:created")
        assert result == "blocked_trigger"

    def test_review_comment_blocked(self):
        assert gate_trigger_whitelist("pull_request_review_comment:created") == "blocked_trigger"

    def test_release_published_unknown(self):
        assert gate_trigger_whitelist("release:published") == "unknown_trigger"


# ── Iteration Cap ─────────────────────────────────────────────

class TestGateIterationCap:

    def test_under_limit_passes(self):
        row = _row(iteration_count=4)
        assert gate_iteration_cap(row, max_iterations=5) is None

    def test_at_limit_blocks(self):
        row = _row(iteration_count=5)
        assert gate_iteration_cap(row, max_iterations=5) == "max_iterations"

    def test_over_limit_blocks(self):
        row = _row(iteration_count=10)
        assert gate_iteration_cap(row, max_iterations=5) == "max_iterations"


# ── Time Cap ──────────────────────────────────────────────────

class TestGateTimeCap:

    def test_recent_pr_passes(self):
        row = _row(created_at=_now() - timedelta(minutes=30))
        assert gate_time_cap(row, max_hours=2) is None

    def test_old_pr_blocks(self):
        row = _row(created_at=_now() - timedelta(hours=3))
        assert gate_time_cap(row, max_hours=2) == "timeout_per_pr"


# ── Cooldown ──────────────────────────────────────────────────

class TestGateCooldown:

    def test_never_reviewed_passes(self):
        row = _row(last_review_at=None)
        assert gate_cooldown(row, cooldown_seconds=300) is None

    def test_recent_review_blocks(self):
        row = _row(last_review_at=_now() - timedelta(seconds=60))
        assert gate_cooldown(row, cooldown_seconds=300) == "cooldown"

    def test_old_review_passes(self):
        row = _row(last_review_at=_now() - timedelta(seconds=400))
        assert gate_cooldown(row, cooldown_seconds=300) is None


# ── ReviewDecision ────────────────────────────────────────────

class TestReviewDecision:

    def test_skip_factory(self):
        d = ReviewDecision.skip("max_iterations")
        assert d.proceed is False
        assert d.reason == "max_iterations"
        assert d.row is None

    def test_advance_factory(self):
        row = _row()
        d = ReviewDecision.advance(row)
        assert d.proceed is True
        assert d.reason == "proceed"
        assert d.row is row


# ── Circuit Breaker (Redis) ───────────────────────────────────

@pytest.fixture
def fake_redis():
    import fakeredis.aioredis
    return fakeredis.aioredis.FakeRedis()


class TestCircuitBreaker:

    @pytest.mark.asyncio
    async def test_first_call_not_open(self, fake_redis):
        is_open, count = await check_circuit_breaker(fake_redis, "owner/repo")
        assert is_open is False
        assert count == 1

    @pytest.mark.asyncio
    async def test_opens_at_threshold(self, fake_redis):
        repo = "owner/repo"
        for _ in range(20):
            await check_circuit_breaker(fake_redis, repo, threshold=20)
        is_open, count = await check_circuit_breaker(fake_redis, repo, threshold=20)
        assert is_open is True
        assert count == 21

    @pytest.mark.asyncio
    async def test_independent_per_repo(self, fake_redis):
        for _ in range(20):
            await check_circuit_breaker(fake_redis, "owner/repo-a", threshold=20)
        is_open_b, count_b = await check_circuit_breaker(fake_redis, "owner/repo-b", threshold=20)
        assert is_open_b is False
        assert count_b == 1
