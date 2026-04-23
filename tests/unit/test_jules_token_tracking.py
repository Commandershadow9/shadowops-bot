"""Unit-Tests fuer Token-Tracking im Jules-Workflow.

Verifiziert dass nach review_pr die AI-Engine-Tokens in
jules_pr_reviews.tokens_consumed landen (nicht mehr hart 0).
"""
from unittest.mock import AsyncMock, MagicMock
import pytest


def _make_gh_mock(ai_engine):
    """Baut einen GitHubIntegration-Mock mit allem was _jules_run_review braucht."""
    from src.integrations.github_integration import jules_workflow_mixin as jwm

    gh = MagicMock()
    gh.config = MagicMock()
    gh.config.jules_workflow = MagicMock(
        few_shot_examples=3, max_diff_chars=8000, max_iterations=5,
        project_knowledge_limit=5, dry_run=False,
    )
    gh.ai_service = ai_engine

    # State-Layer
    state = MagicMock()
    state.store_review_result = AsyncMock()
    state.mark_reviewed_sha = AsyncMock()
    state.release_lock = AsyncMock()
    gh.jules_state = state

    # Learning-Loader
    learning = MagicMock()
    learning.fetch_project_knowledge = AsyncMock(return_value=[])
    learning.fetch_few_shot_examples = AsyncMock(return_value=[])
    gh.jules_learning = learning

    # Helper-Methoden stubben
    gh._jules_load_finding = AsyncMock(return_value={
        "title": "t", "severity": "high", "category": "test",
    })
    gh._jules_fetch_diff = AsyncMock(return_value="diff --git a/x b/x")
    gh._jules_post_or_edit_comment = AsyncMock()
    gh._jules_discord_review_embed = AsyncMock()
    gh._handle_approval_with_adapter = AsyncMock()
    gh._jules_escalate = AsyncMock()
    gh._jules_discord_alarm = AsyncMock()
    gh._get_agent_detector = MagicMock(return_value=MagicMock(detect=lambda p: None))

    return gh, jwm


def _valid_review():
    return {
        "verdict": "approved",
        "summary": "LGTM",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "matches"},
    }


@pytest.mark.asyncio
async def test_store_review_result_receives_real_tokens_from_ai_engine():
    """Reviewer schreibt den tatsaechlichen Token-Verbrauch, nicht 0."""
    ai = MagicMock()
    ai.review_pr = AsyncMock(return_value=_valid_review())
    ai._last_token_usage = {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}

    gh, jwm = _make_gh_mock(ai)
    row = MagicMock(id=42, finding_id=7, review_comment_id=None, iteration_count=0)

    await jwm.JulesWorkflowMixin._jules_run_review(
        gh, repo="repo", pr_number=99, head_sha="abc123", row=row,
        pr_payload={"title": "test", "body": "", "labels": []},
    )

    assert gh.jules_state.store_review_result.await_count == 1
    call = gh.jules_state.store_review_result.await_args
    # Signatur: (row_id, review, blockers, tokens=...)
    if call.args and len(call.args) >= 4:
        tokens_arg = call.args[3]
    else:
        tokens_arg = call.kwargs.get("tokens")
    assert tokens_arg == 1200, (
        f"Expected tokens=1200 from ai._last_token_usage, "
        f"got {tokens_arg}. Das hardcoded 'tokens=0' ist zurueck."
    )


@pytest.mark.asyncio
async def test_store_review_result_uses_zero_when_usage_missing():
    """Wenn AI-Engine keine Tokens meldet (Fallback), bleibt tokens_consumed=0 (nicht crashen)."""
    ai = MagicMock(spec=["review_pr"])  # kein _last_token_usage Attribut
    ai.review_pr = AsyncMock(return_value=_valid_review())

    gh, jwm = _make_gh_mock(ai)
    row = MagicMock(id=42, finding_id=None, review_comment_id=None, iteration_count=0)

    await jwm.JulesWorkflowMixin._jules_run_review(
        gh, repo="r", pr_number=1, head_sha="s", row=row,
        pr_payload={"title": "", "body": "", "labels": []},
    )

    call = gh.jules_state.store_review_result.await_args
    if call.args and len(call.args) >= 4:
        tokens_arg = call.args[3]
    else:
        tokens_arg = call.kwargs.get("tokens")
    assert tokens_arg == 0


@pytest.mark.asyncio
async def test_store_review_result_zero_when_usage_is_none():
    """Defensive: _last_token_usage=None (statt Dict) fuehrt nicht zum Crash."""
    ai = MagicMock()
    ai.review_pr = AsyncMock(return_value=_valid_review())
    ai._last_token_usage = None

    gh, jwm = _make_gh_mock(ai)
    row = MagicMock(id=42, finding_id=None, review_comment_id=None, iteration_count=0)

    await jwm.JulesWorkflowMixin._jules_run_review(
        gh, repo="r", pr_number=2, head_sha="s", row=row,
        pr_payload={"title": "", "body": "", "labels": []},
    )
    call = gh.jules_state.store_review_result.await_args
    if call.args and len(call.args) >= 4:
        tokens_arg = call.args[3]
    else:
        tokens_arg = call.kwargs.get("tokens")
    assert tokens_arg == 0
