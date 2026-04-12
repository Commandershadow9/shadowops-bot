# tests/unit/test_ai_engine_review_pr.py
"""Tests fuer AIEngine.review_pr() — strukturiertes PR-Review mit Schema-Validierung."""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock


def _valid_review():
    return {
        "verdict": "approved",
        "summary": "Clean upgrade",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "matches finding"},
    }


def _make_engine():
    """Create a minimal AIEngine-like object with mocked claude provider."""
    from src.integrations import ai_engine as aie
    engine = aie.AIEngine.__new__(aie.AIEngine)
    engine.logger = logging.getLogger("test_jules")
    engine.claude = MagicMock()
    return engine


@pytest.mark.asyncio
async def test_review_pr_returns_validated_dict():
    engine = _make_engine()
    engine.claude.query_raw = AsyncMock(return_value=json.dumps(_valid_review()))

    result = await engine.review_pr(
        diff="diff --git a/x b/x", finding_context={"title": "t", "severity": "high"},
        project="test", iteration=1, project_knowledge=[], few_shot_examples=[],
    )
    assert result is not None
    assert result["verdict"] == "approved"
    assert result["scope_check"]["in_scope"] is True


@pytest.mark.asyncio
async def test_review_pr_invalid_json_returns_none():
    engine = _make_engine()
    engine.claude.query_raw = AsyncMock(return_value="not json")

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_schema_invalid_returns_none():
    engine = _make_engine()
    engine.claude.query_raw = AsyncMock(return_value=json.dumps({"verdict": "approved"}))

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None


@pytest.mark.asyncio
async def test_review_pr_verdict_overridden_deterministic():
    engine = _make_engine()
    bad = _valid_review()
    bad["scope_check"]["in_scope"] = False
    engine.claude.query_raw = AsyncMock(return_value=json.dumps(bad))

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result["verdict"] == "revision_requested"


@pytest.mark.asyncio
async def test_review_pr_strips_markdown_fences():
    engine = _make_engine()
    fenced = "```json\n" + json.dumps(_valid_review()) + "\n```"
    engine.claude.query_raw = AsyncMock(return_value=fenced)

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is not None
    assert result["verdict"] == "approved"


@pytest.mark.asyncio
async def test_review_pr_claude_exception_returns_none():
    engine = _make_engine()
    engine.claude.query_raw = AsyncMock(side_effect=Exception("timeout"))

    result = await engine.review_pr(
        diff="d", finding_context={}, project="p", iteration=1,
        project_knowledge=[], few_shot_examples=[],
    )
    assert result is None
