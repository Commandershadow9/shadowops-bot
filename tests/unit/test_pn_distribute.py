"""Tests für Stufe 5: Distribute."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from patch_notes.stages.distribute import (
    _build_embed, _type_to_emoji, _log_metrics, distribute,
)
from patch_notes.context import PipelineContext


def _make_ctx(**kwargs) -> PipelineContext:
    defaults = dict(
        project="test-project",
        project_config={"patch_notes": {"type": "devops", "language": "de"}, "color": 0x00FF00},
        raw_commits=[{"message": "feat: test"}],
        trigger="cron",
        version="1.2.0",
        title="Super Update",
        tldr="Tolles Update mit vielen Features",
        web_content="Langer Web-Content",
        changes=[
            {"type": "feature", "description": "Neues Feature", "details": ["Detail 1"]},
            {"type": "fix", "description": "Bug gefixt"},
        ],
        git_stats={"commits": 15, "files_changed": 30, "lines_added": 500},
        team_credits=[{"name": "Shadow", "role": "Lead Dev", "commits": 12}],
        groups=[{"tag": "FEATURE", "is_player_facing": True}],
        update_size="normal",
    )
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def test_build_embed_title():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert "v1.2.0" in embed.title
    assert "Super Update" in embed.title


def test_build_embed_color():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert embed.color.value == 0x00FF00


def test_build_embed_tldr_in_description():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert "Tolles Update" in embed.description


def test_build_embed_changes_as_fields():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert len(embed.fields) == 2


def test_build_embed_footer_stats():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert "15 Commits" in embed.footer.text
    assert "Shadow" in embed.footer.text


def test_build_embed_changelog_url():
    ctx = _make_ctx(project_config={
        "patch_notes": {"changelog_url": "https://example.com/changelog"},
        "color": 0xFF0000,
    })
    embed = _build_embed(ctx)
    assert embed.url == "https://example.com/changelog/1-2-0"


def test_build_embed_no_changelog_url():
    ctx = _make_ctx()
    embed = _build_embed(ctx)
    assert embed.url is None


def test_type_to_emoji():
    assert _type_to_emoji("feature") == "🆕"
    assert _type_to_emoji("fix") == "🐛"
    assert _type_to_emoji("unknown") == "📝"


def test_build_embed_fallback_web_content():
    """Ohne Changes: Web-Content als Description."""
    ctx = _make_ctx(changes=[])
    embed = _build_embed(ctx)
    assert "Langer Web-Content" in embed.description


@pytest.mark.asyncio
async def test_distribute_no_bot():
    """Ohne Bot: Distribution wird übersprungen."""
    ctx = _make_ctx()
    await distribute(ctx, bot=None)
    assert ctx.sent_message_ids == []


def test_log_metrics(capsys):
    """Metriken werden als JSON geloggt."""
    ctx = _make_ctx()
    _log_metrics(ctx)
    # Prüfe dass kein Fehler auftritt — Metriken gehen an Logger, nicht stdout
