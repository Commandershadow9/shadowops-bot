"""Tests fuer JulesWorkflowMixin — should_review Gate-Pipeline + handle_pr_event."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import fakeredis.aioredis

from src.integrations.github_integration.jules_workflow_mixin import JulesWorkflowMixin
from src.integrations.github_integration.jules_gates import ReviewDecision
from src.integrations.github_integration.jules_state import JulesReviewRow


# ── Helper ────────────────────────────────────────────────────

def _row(**overrides) -> JulesReviewRow:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1, repo="X", pr_number=1, issue_number=None, finding_id=None,
        status="reviewing", last_reviewed_sha=None, iteration_count=0,
        last_review_at=None, lock_acquired_at=now, lock_owner="w",
        review_comment_id=None, last_review_json=None, last_blockers=None,
        tokens_consumed=0, created_at=now, updated_at=now,
        closed_at=None, human_override=False)
    defaults.update(overrides)
    return JulesReviewRow(**defaults)


class _Cfg:
    class circuit_breaker:
        max_reviews_per_hour = 20
        pause_duration_seconds = 3600
    enabled = True
    dry_run = False
    max_iterations = 5
    cooldown_seconds = 300
    max_hours_per_pr = 2
    max_diff_chars = 8000
    few_shot_examples = 3
    project_knowledge_limit = 10
    notification_channel = "test"
    escalation_channel = "test"
    role_ping_on_escalation = "@Test"


class _Harness(JulesWorkflowMixin):
    def __init__(self):
        self.jules_state = MagicMock()
        self.jules_state.process_id = "test-worker"
        self.jules_state.try_claim_review = AsyncMock(return_value=None)
        self.jules_state.mark_terminal = AsyncMock()
        self.jules_state.release_lock = AsyncMock()
        self.jules_state.ensure_pending = AsyncMock(return_value=_row())
        self.jules_state.get = AsyncMock(return_value=None)
        self.jules_learning = MagicMock()
        self.ai_service = MagicMock()
        self.redis = fakeredis.aioredis.FakeRedis()
        self.config = MagicMock()
        self.config.jules_workflow = _Cfg()
        self.config.jules_workflow.circuit_breaker = _Cfg.circuit_breaker
        self.bot = MagicMock()
        self.bot.discord_logger = None


@pytest.fixture
def h():
    return _Harness()


# ── should_review Tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_should_review_skips_when_disabled(h):
    h.config.jules_workflow.enabled = False
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert not d.proceed and d.reason == "feature_disabled"


@pytest.mark.asyncio
async def test_should_review_blocks_issue_comment(h):
    d = await h.should_review("X", 1, "sha", "issue_comment:created")
    assert not d.proceed and d.reason == "blocked_trigger"
    h.jules_state.try_claim_review.assert_not_called()


@pytest.mark.asyncio
async def test_should_review_skips_locked(h):
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert not d.proceed and d.reason == "already_reviewed_or_locked"


@pytest.mark.asyncio
async def test_should_review_escalates_max_iterations(h):
    h.jules_state.try_claim_review.return_value = _row(iteration_count=5)
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert not d.proceed and d.reason == "max_iterations"
    h.jules_state.mark_terminal.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_escalates_timeout(h):
    h.jules_state.try_claim_review.return_value = _row(
        created_at=datetime.now(timezone.utc) - timedelta(hours=3))
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert not d.proceed and d.reason == "timeout_per_pr"


@pytest.mark.asyncio
async def test_should_review_skips_cooldown(h):
    h.jules_state.try_claim_review.return_value = _row(
        last_review_at=datetime.now(timezone.utc) - timedelta(seconds=60))
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert not d.proceed and d.reason == "cooldown"
    h.jules_state.release_lock.assert_called_once()


@pytest.mark.asyncio
async def test_should_review_proceeds_fresh(h):
    h.jules_state.try_claim_review.return_value = _row()
    d = await h.should_review("X", 1, "sha", "pull_request:opened")
    assert d.proceed and d.row is not None


# ── handle_jules_pr_event Tests ──────────────────────────

def _pr_payload(action="opened", labels=None, author="google-labs-jules[bot]",
                body="Fixes #42", merged=False):
    return {
        "action": action,
        "pull_request": {
            "number": 1, "head": {"sha": "abc123"},
            "user": {"login": author}, "body": body,
            "labels": [{"name": "jules"}] if labels is None else labels,
            "merged": merged,
        },
        "repository": {"name": "X", "owner": {"login": "o"}},
    }


@pytest.mark.asyncio
async def test_handle_pr_ignores_non_jules(h):
    payload = _pr_payload(author="SomeHuman", labels=[])
    await h.handle_jules_pr_event(payload)
    h.jules_state.ensure_pending.assert_not_called()


@pytest.mark.asyncio
async def test_handle_pr_calls_should_review(h):
    h.should_review = AsyncMock(return_value=ReviewDecision.skip("test"))
    await h.handle_jules_pr_event(_pr_payload())
    h.jules_state.ensure_pending.assert_called_once()
    h.should_review.assert_called_once()


@pytest.mark.asyncio
async def test_handle_pr_close_marks_merged(h):
    h.jules_state.get.return_value = _row(status="approved")
    await h.handle_jules_pr_event(_pr_payload(action="closed", merged=True))
    h.jules_state.mark_terminal.assert_called_once_with(1, "merged")


@pytest.mark.asyncio
async def test_dry_run_skips_ai_and_releases_lock(h):
    """Dry-Run-Mode: kein AI-Call, Lock wird freigegeben."""
    h.config.jules_workflow.dry_run = True
    h.jules_state.try_claim_review.return_value = _row()
    h.ai_service.review_pr = AsyncMock()
    h.jules_state.ensure_pending = AsyncMock(return_value=_row())

    # Mock _jules_is_jules_pr to return True
    h._jules_is_jules_pr = AsyncMock(return_value=True)
    h._jules_lookup_finding = AsyncMock(return_value=None)

    payload = _pr_payload()
    await h.handle_jules_pr_event(payload)

    h.ai_service.review_pr.assert_not_called()
    h.jules_state.release_lock.assert_called()
