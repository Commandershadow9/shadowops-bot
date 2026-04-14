"""Tests für Stufe 3: Generate — Template + AI-Call."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from patch_notes.stages.generate import (
    generate, _build_structured_wrapper, _try_parse_json,
    _collect_feature_teasers, _get_ai_service,
)
from patch_notes.context import PipelineContext


def _make_ctx(**kwargs) -> PipelineContext:
    defaults = dict(
        project="test-project",
        project_config={"patch_notes": {"type": "devops", "language": "de"}},
        raw_commits=[{"message": "feat: test", "sha": "abc"}],
        trigger="manual",
        groups=[{"tag": "FEATURE", "theme": "Test", "scope": "core",
                 "commits": [{"message": "feat: test"}], "summary": "Test feature",
                 "is_player_facing": True, "pr_labels": []}],
        version="1.0.0",
        update_size="normal",
    )
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def test_try_parse_json_direct():
    result = _try_parse_json('{"title": "Test"}')
    assert result == {"title": "Test"}


def test_try_parse_json_markdown_fence():
    text = "Here is the result:\n```json\n{\"title\": \"Test\"}\n```\nDone."
    result = _try_parse_json(text)
    assert result == {"title": "Test"}


def test_try_parse_json_brace_extraction():
    text = 'Some text before {"title": "Test"} and after'
    result = _try_parse_json(text)
    assert result == {"title": "Test"}


def test_try_parse_json_invalid():
    assert _try_parse_json("no json here") is None
    assert _try_parse_json("") is None


def test_structured_wrapper_de():
    ctx = _make_ctx()
    wrapper = _build_structured_wrapper("test prompt", ctx)
    assert "JSON" in wrapper
    assert "KEINE Version" in wrapper
    assert "test prompt" in wrapper


def test_structured_wrapper_en():
    ctx = _make_ctx(project_config={"patch_notes": {"language": "en"}})
    wrapper = _build_structured_wrapper("test prompt", ctx)
    assert "NO version" in wrapper


def test_get_ai_service_no_bot():
    assert _get_ai_service(None) is None


def test_get_ai_service_with_bot():
    bot = MagicMock()
    bot.github_integration.ai_service = "mock_service"
    assert _get_ai_service(bot) == "mock_service"


@pytest.mark.asyncio
async def test_generate_no_ai_service():
    """Ohne AI-Service: ai_result bleibt None."""
    ctx = _make_ctx()
    await generate(ctx, bot=None)
    assert ctx.ai_result is None
    assert ctx.prompt != ""  # Prompt wurde trotzdem gebaut
    assert "test-project" in ctx.prompt


@pytest.mark.asyncio
async def test_generate_with_mock_ai():
    """Mit Mock-AI: Structured Output wird zurückgegeben."""
    mock_ai = AsyncMock()
    mock_ai.generate_structured_patch_notes.return_value = {
        "title": "Super Update",
        "tldr": "Tolles Update",
        "web_content": "Content",
        "changes": [{"type": "feature", "description": "Neues Feature"}],
    }
    mock_ai._last_engine = "codex"

    bot = MagicMock()
    bot.github_integration.ai_service = mock_ai

    ctx = _make_ctx()
    await generate(ctx, bot=bot)

    assert ctx.ai_result is not None
    assert ctx.ai_result["title"] == "Super Update"
    assert ctx.ai_engine_used == "codex"
    assert ctx.generation_time_s >= 0


@pytest.mark.asyncio
async def test_generate_structured_fails_raw_fallback():
    """Structured Output fails → Raw Fallback mit JSON-Parsing."""
    mock_ai = AsyncMock()
    mock_ai.generate_structured_patch_notes.side_effect = Exception("structured failed")
    mock_ai.get_raw_ai_response.return_value = '{"title": "Fallback", "tldr": "test"}'
    mock_ai._last_engine = "claude"

    bot = MagicMock()
    bot.github_integration.ai_service = mock_ai

    ctx = _make_ctx()
    await generate(ctx, bot=bot)

    assert ctx.ai_result is not None
    assert ctx.ai_result["title"] == "Fallback"


def test_collect_teasers_no_path():
    ctx = _make_ctx(project_config={"patch_notes": {"type": "devops"}, "path": ""})
    assert _collect_feature_teasers(ctx) == ""
