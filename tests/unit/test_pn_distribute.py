"""Tests für Stufe 5: Distribute — Embeds, Sending, Rollback."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from patch_notes.stages.distribute import (
    _build_full_embed, _build_summary_embed, _build_footer_text,
    _type_to_emoji, _log_metrics, _truncate_description,
    _split_embed_for_sending, _format_change_line,
    _send_customer,
    distribute, retract_patch_notes,
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
        git_stats={"commits": 15, "files_changed": 30, "lines_added": 500, "lines_removed": 80},
        team_credits=[{"name": "Shadow", "role": "Lead Dev", "commits": 12}],
        groups=[{"tag": "FEATURE", "is_player_facing": True}],
        update_size="normal",
    )
    defaults.update(kwargs)
    return PipelineContext(**defaults)


# ── Full Embed Tests ──


def test_build_full_embed_title():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert "v1.2.0" in embed.title
    assert "Super Update" in embed.title


def test_build_full_embed_color():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert embed.color.value == 0x00FF00


def test_build_full_embed_tldr():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert "Tolles Update" in embed.description


def test_build_full_embed_has_category_headers():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert "🆕 Neue Features" in embed.description
    assert "🐛 Bugfixes" in embed.description


def test_build_full_embed_inline_credits():
    ctx = _make_ctx(changes=[
        {"type": "feature", "description": "Neues Feature", "author": "Shadow"},
        {"type": "fix", "description": "Bug gefixt"},
    ])
    embed = _build_full_embed(ctx)
    assert "Shadow" in embed.description
    assert "→" in embed.description


def test_build_full_embed_details_shown():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert "Detail 1" in embed.description


def test_build_full_embed_fallback_web_content():
    ctx = _make_ctx(changes=[])
    embed = _build_full_embed(ctx)
    assert "Langer Web-Content" in embed.description


def test_build_full_embed_no_url():
    ctx = _make_ctx()
    embed = _build_full_embed(ctx)
    assert embed.url is None


# ── Summary Embed Tests ──


def test_build_summary_embed_has_url():
    ctx = _make_ctx()
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert embed.url == "https://example.com/changelog/1-2-0"


def test_build_summary_embed_has_link():
    ctx = _make_ctx()
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "Alle Details" in embed.description
    assert "example.com" in embed.description


def test_build_summary_embed_max_highlights_by_size():
    """Highlight-Count je update_size: small=3, normal=5, big=6, major=8, mega=10."""
    changes = [{"type": "feature", "description": f"Feature {i}"} for i in range(15)]

    # normal: 5 Highlights → 10 weitere
    ctx = _make_ctx(changes=changes, update_size="normal")
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "+10 weitere" in embed.description

    # mega: 10 Highlights → 5 weitere
    ctx = _make_ctx(changes=changes, update_size="mega")
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "+5 weitere" in embed.description


def test_build_summary_embed_mega_has_hero_stats():
    """Bei mega/major MUSS eine Hero-Stats-Zeile mit Commits-Count oben drin sein."""
    ctx = _make_ctx(
        update_size="mega",
        git_stats={"commits": 73, "files_changed": 87, "lines_added": 9399, "lines_removed": 312},
    )
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "73 Commits" in embed.description
    assert "87 Dateien" in embed.description


def test_build_summary_embed_small_uses_blockquote():
    """Kleine Updates bleiben kompakt mit Blockquote-TL;DR (kein Hype)."""
    ctx = _make_ctx(update_size="small", tldr="Kleiner Fix")
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "> Kleiner Fix" in embed.description
    assert "🚀" not in (embed.title or "")


def test_build_summary_embed_inline_credits():
    ctx = _make_ctx(changes=[
        {"type": "feature", "description": "Feature X", "author": "Shadow"},
    ])
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "Shadow" in embed.description


# ── Footer Tests ──


def test_footer_contains_stats():
    ctx = _make_ctx()
    footer = _build_footer_text(ctx)
    assert "v1.2.0" in footer
    assert "15 Commits" in footer
    assert "Shadow" in footer
    assert "+500/-80" in footer


# ── Embed Splitting ──


def test_split_short_embed_no_split():
    import discord
    embed = discord.Embed(title="Test", description="Short text")
    result = _split_embed_for_sending(embed)
    assert len(result) == 1


def test_split_long_embed():
    import discord
    long_text = "Line\n" * 2000  # ~10000 Zeichen
    embed = discord.Embed(title="Test", description=long_text, color=0xFF0000)
    embed.set_footer(text="Footer")
    result = _split_embed_for_sending(embed)
    assert len(result) >= 2
    assert result[0].title == "Test"
    assert result[1].title is None  # Nur erster Embed hat Titel


# ── Truncation ──


def test_truncate_short():
    assert _truncate_description("kurz") == "kurz"


def test_truncate_long():
    long = "x" * 5000
    result = _truncate_description(long)
    assert len(result) <= 4096
    assert "gekürzt" in result


# ── Emoji Mapping ──


def test_type_to_emoji():
    assert _type_to_emoji("feature") == "🆕"
    assert _type_to_emoji("fix") == "🐛"
    assert _type_to_emoji("unknown") == "📝"


# ── Format Change Line ──


def test_format_change_line_with_author():
    change = {"description": "Neues Feature", "author": "Shadow"}
    assert _format_change_line(change, True) == "→ Neues Feature · *Shadow*"


def test_format_change_line_without_author():
    change = {"description": "Neues Feature"}
    assert _format_change_line(change, True) == "→ Neues Feature"


def test_format_change_line_author_hidden():
    change = {"description": "Fix", "author": "Shadow"}
    assert _format_change_line(change, False) == "→ Fix"


# ── Breaking Changes in Full Embed ──


def test_build_full_embed_breaking_changes():
    ctx = _make_ctx(
        ai_result={
            "title": "Update", "tldr": "TL;DR",
            "changes": [{"type": "feature", "description": "Feature"}],
            "breaking_changes": ["API v1 entfernt"],
        }
    )
    embed = _build_full_embed(ctx)
    assert "Breaking Changes" in embed.description
    assert "API v1 entfernt" in embed.description


# ── Distribution ──


@pytest.mark.asyncio
async def test_distribute_no_bot():
    ctx = _make_ctx()
    await distribute(ctx, bot=None)
    assert ctx.sent_message_ids == []


@pytest.mark.asyncio
async def test_send_customer_reads_channel_id_from_top_level(monkeypatch):
    """Regression: bot.py injiziert update_channel_id auf Top-Level des project_config.
    _send_customer muss Top-Level als Fallback lesen (Vorfall 2026-04-14, messages_sent=0)."""
    ctx = _make_ctx(project_config={
        "patch_notes": {"type": "devops", "language": "de"},
        "color": 0x00FF00,
        "update_channel_id": 1234567890,
        "internal_channel_id": 9876543210,
    })

    sent_channel_ids = []

    async def fake_send_to_channel(bot, channel_id, embed, ctx, **kwargs):
        sent_channel_ids.append(channel_id)

    monkeypatch.setattr("patch_notes.stages.distribute._send_to_channel", fake_send_to_channel)

    await _send_customer(bot=MagicMock(), embed=MagicMock(), ctx=ctx)

    assert 1234567890 in sent_channel_ids
    assert 9876543210 in sent_channel_ids


@pytest.mark.asyncio
async def test_send_customer_prefers_patch_notes_nested(monkeypatch):
    """Wenn jemand patch_notes.update_channel_id manuell in config.yaml pflegt, hat das Vorrang."""
    ctx = _make_ctx(project_config={
        "patch_notes": {"type": "devops", "update_channel_id": 1111111111},
        "color": 0x00FF00,
        "update_channel_id": 2222222222,
    })

    sent_channel_ids = []

    async def fake_send_to_channel(bot, channel_id, embed, ctx, **kwargs):
        sent_channel_ids.append(channel_id)

    monkeypatch.setattr("patch_notes.stages.distribute._send_to_channel", fake_send_to_channel)

    await _send_customer(bot=MagicMock(), embed=MagicMock(), ctx=ctx)

    assert 1111111111 in sent_channel_ids
    assert 2222222222 not in sent_channel_ids


# ── Metriken ──


def test_log_metrics_no_crash():
    ctx = _make_ctx()
    _log_metrics(ctx)  # Darf nicht crashen
