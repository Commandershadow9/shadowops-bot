"""Tests für Stufe 5: Distribute — Embeds, Sending, Rollback."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
from patch_notes.stages.distribute import (
    _build_full_embed, _build_summary_embed, _build_footer_text,
    _type_to_emoji, _log_metrics, _truncate_description,
    _split_embed_for_sending, _format_change_line,
    _send_customer,
    distribute, retract_patch_notes,
    _archive_release_notes,
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


def test_authors_display_formats():
    """_authors_display: 1 -> 'X', 2 -> 'X + Y', 3+ -> 'X, Y, Z'."""
    from patch_notes.stages.distribute import _authors_display
    assert _authors_display({"authors": ["Shadow"]}) == "Shadow"
    assert _authors_display({"authors": ["Shadow", "Mapu"]}) == "Shadow + Mapu"
    assert _authors_display({"authors": ["Shadow", "Mapu", "Renji"]}) == "Shadow, Mapu, Renji"
    # Fallback auf single author
    assert _authors_display({"author": "Shadow"}) == "Shadow"
    # authors hat Vorrang vor single author
    assert _authors_display({"author": "Alt", "authors": ["Shadow"]}) == "Shadow"
    # Nichts gesetzt
    assert _authors_display({}) == ""


def test_build_summary_embed_mega_shows_multi_authors():
    """Mega-Embed rendert authors-Liste als dezente Sub-Zeile."""
    ctx = _make_ctx(
        update_size="mega",
        changes=[{
            "type": "feature",
            "description": "Kaskaden-System",
            "details": ["Rueckzuendung per Timer"],
            "authors": ["Shadow", "Mapu"],
        }],
    )
    embed = _build_summary_embed(ctx, "https://example.com/changelog")
    assert "Shadow + Mapu" in embed.description


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


def test_log_metrics_ai_engine_from_context(caplog):
    """ai_engine-Feld muss aus ctx.ai_engine_used kommen, nicht 'unknown' sein."""
    import json as _json
    import logging as _logging
    ctx = _make_ctx(ai_engine_used="codex")
    with caplog.at_level(_logging.INFO, logger="shadowops"):
        _log_metrics(ctx)
    metrics_records = [r for r in caplog.records if "METRICS|patch_notes_pipeline|" in r.getMessage()]
    assert metrics_records, "Kein METRICS-Log gefunden"
    raw_json = metrics_records[-1].getMessage().split("METRICS|patch_notes_pipeline|", 1)[1]
    payload = _json.loads(raw_json)
    assert payload["ai_engine"] == "codex"


def test_log_metrics_pipeline_total_time_from_monotonic(monkeypatch, caplog):
    """pipeline_total_time_s wird aus ctx.pipeline_start_monotonic abgeleitet."""
    import json as _json
    import logging as _logging
    import time as _time

    # Pipeline-Start simulieren: 42.5s vor jetzt
    fake_now = 1000.0
    monkeypatch.setattr(_time, "monotonic", lambda: fake_now)
    ctx = _make_ctx(pipeline_start_monotonic=fake_now - 42.5)

    with caplog.at_level(_logging.INFO, logger="shadowops"):
        _log_metrics(ctx)
    metrics_records = [r for r in caplog.records if "METRICS|patch_notes_pipeline|" in r.getMessage()]
    assert metrics_records, "Kein METRICS-Log gefunden"
    raw_json = metrics_records[-1].getMessage().split("METRICS|patch_notes_pipeline|", 1)[1]
    payload = _json.loads(raw_json)
    assert payload["pipeline_total_time_s"] == 42.5


def test_log_metrics_pipeline_total_time_fallback_to_metrics_dict(caplog):
    """Falls pipeline_start_monotonic None ist, fällt Builder auf ctx.metrics-Dict zurück."""
    import json as _json
    import logging as _logging
    ctx = _make_ctx()
    ctx.metrics["pipeline_total_time_s"] = 12.3
    with caplog.at_level(_logging.INFO, logger="shadowops"):
        _log_metrics(ctx)
    metrics_records = [r for r in caplog.records if "METRICS|patch_notes_pipeline|" in r.getMessage()]
    assert metrics_records, "Kein METRICS-Log gefunden"
    raw_json = metrics_records[-1].getMessage().split("METRICS|patch_notes_pipeline|", 1)[1]
    payload = _json.loads(raw_json)
    assert payload["pipeline_total_time_s"] == 12.3


# ── _archive_release_notes: git commit + push (shadowops#302) ──
#
# Bug: Die Archivierung schrieb release_notes.md (Template-Reset) + das Archiv,
# committete/pushte aber nie → Deploy-Repo blieb dirty → nächster git pull des
# Auto-Deploys brach ab. Diese Tests pinnen das defensive commit+push-Verhalten.

# Default-Antworten je git-Subkommando. diff --cached --quiet = rc 1 bedeutet
# "es gibt gestagte Änderungen" → committen.
_GIT_DEFAULTS = {
    "rev-parse": (0, ".git\n", ""),
    "symbolic-ref": (0, "main\n", ""),
    "add": (0, "", ""),
    "diff": (1, "", ""),
    "config:user.email": (0, "bot@shadowops\n", ""),
    "config:user.name": (0, "ShadowOps Bot\n", ""),
    "commit": (0, "", ""),
    "push": (0, "", ""),
}


def _git_responder(overrides=None):
    """Fake für patch_notes.stages.distribute.subprocess.run.

    Gibt (fake_run, calls) zurück. `calls` sammelt jede git-Argumentliste.
    `overrides` (dict) ersetzt Default-Antworten pro Marker-Key.
    """
    overrides = overrides or {}
    calls: list[list[str]] = []

    def _key_for(cmd: list[str]) -> str:
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub == "config":
            return "config:" + (cmd[2] if len(cmd) > 2 else "")
        return sub

    def fake_run(cmd, **kwargs):
        cmd = list(cmd)
        calls.append(cmd)
        key = _key_for(cmd)
        rc, out, err = overrides.get(key, _GIT_DEFAULTS.get(key, (0, "", "")))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return fake_run, calls


def _archive_ctx(tmp_path, **kwargs):
    cfg = {"path": str(tmp_path), "patch_notes": {"type": "devops", "language": "de"}}
    kwargs.setdefault("version", "1.2.3")
    return _make_ctx(project_config=cfg, **kwargs)


def _seed_release_notes(tmp_path, body="## Echtes Feature\nEin Eintrag mit deutlich mehr als zwanzig Zeichen Inhalt.\n"):
    notes = tmp_path / "release_notes.md"
    notes.write_text(f"<!-- template-kommentar -->\n\n{body}", encoding="utf-8")
    return notes


def _git_calls(calls, subcommand):
    """Filtert calls auf ein git-Subkommando (cmd[1])."""
    return [c for c in calls if len(c) > 1 and c[1] == subcommand]


def test_archive_commits_only_two_files(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)

    add_calls = _git_calls(calls, "add")
    assert add_calls, "git add wurde nicht aufgerufen"
    add = add_calls[0]
    assert "--" in add, "Pathspec-Separator -- fehlt"
    assert any("release_notes.md" in a for a in add)
    assert any("v1.2.3.md" in a for a in add)


def test_archive_never_uses_git_add_all(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)

    for c in calls:
        assert c[:3] != ["git", "add", "-A"], f"Verbotenes git add -A: {c}"


def test_archive_git_failure_does_not_raise(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, _ = _git_responder(overrides={"commit": (1, "", "boom")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    # darf nicht werfen; Archiv muss trotzdem auf Platte liegen
    _archive_release_notes(ctx)
    assert (tmp_path / "docs" / "release-history" / "v1.2.3.md").exists()


def test_archive_subprocess_exception_does_not_raise(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)

    def boom(cmd, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", boom)
    _archive_release_notes(ctx)  # kein Crash
    assert (tmp_path / "docs" / "release-history" / "v1.2.3.md").exists()


def test_archive_not_a_git_repo_skips_quietly(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder(overrides={"rev-parse": (128, "", "not a git repo")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert not _git_calls(calls, "commit"), "Commit trotz Nicht-Git-Repo"
    assert not _git_calls(calls, "push")


def test_archive_detached_head_skips_push(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder(overrides={"symbolic-ref": (1, "", "detached")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert not _git_calls(calls, "push"), "Push trotz detached HEAD"
    assert not _git_calls(calls, "commit"), "Commit trotz detached HEAD (early return fehlt)"


def test_archive_commit_uses_bot_identity_via_c_flags(tmp_path, monkeypatch):
    # Bot-Identität wird via `git -c` pro Commit gesetzt — NICHT in .git/config
    # geschrieben (keine Identity-Pollution im Deploy-Repo).
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)

    commit_calls = _git_calls(calls, "commit")
    assert commit_calls, "Kein commit-Call"
    flat = commit_calls[0]
    assert "-c" in flat
    assert "user.email=shadowops@local" in flat
    assert "user.name=ShadowOps Bot" in flat
    # NIEMALS persistentes git config user.* schreiben (4-arg config-set)
    config_sets = [c for c in calls if len(c) >= 4 and c[1] == "config"]
    assert not config_sets, f"Persistentes git config geschrieben: {config_sets}"


def test_archive_idempotent_nothing_staged(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    # diff --cached --quiet rc 0 = nichts gestaged → kein commit
    fake_run, calls = _git_responder(overrides={"diff": (0, "", "")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert not _git_calls(calls, "commit"), "Commit trotz nichts-gestaged"


def test_archive_push_failure_keeps_local_commit(tmp_path, monkeypatch, caplog):
    import logging
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder(overrides={"push": (1, "", "no upstream")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    with caplog.at_level(logging.WARNING, logger="shadowops"):
        _archive_release_notes(ctx)

    assert _git_calls(calls, "commit"), "Commit fehlt"
    assert _git_calls(calls, "push"), "Push wurde nicht versucht"
    # kein Raise; Warnung geloggt
    assert any("push" in r.getMessage().lower() for r in caplog.records)


def test_archive_commit_message_contains_version(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)

    commit_calls = _git_calls(calls, "commit")
    assert commit_calls, "Kein commit-Call"
    msg_idx = commit_calls[0].index("-m") + 1
    # Exakte Conventional-Commit-Form pinnen (nicht nur Substring)
    assert commit_calls[0][msg_idx] == "docs: archive release notes v1.2.3"


def test_archive_template_only_skips_git_entirely(tmp_path, monkeypatch):
    # release_notes.md enthält nur einen HTML-Kommentar → kein Archiv, kein git
    notes = tmp_path / "release_notes.md"
    notes.write_text("<!-- nur template, kein inhalt -->\n", encoding="utf-8")
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert calls == [], "git wurde trotz Template-only aufgerufen"


def test_archive_commits_when_changes_staged(tmp_path, monkeypatch):
    # Gegenrichtung zu test_archive_idempotent_nothing_staged (Mutation-Coverage):
    # diff --cached --quiet rc 1 = es GIBT gestagte Änderungen → commit MUSS laufen.
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder(overrides={"diff": (1, "", "")})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert _git_calls(calls, "commit"), "Commit fehlt obwohl Änderungen gestaged sind"


def test_archive_paths_outside_repo_skip(tmp_path, monkeypatch):
    # relative_to(base) wirft ValueError → Commit wird übersprungen, kein Crash.
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    from pathlib import Path as _P
    orig = _P.relative_to

    def _raise(self, *a, **k):
        raise ValueError("outside repo")

    monkeypatch.setattr(_P, "relative_to", _raise)
    _archive_release_notes(ctx)  # kein Crash
    monkeypatch.setattr(_P, "relative_to", orig)

    assert not _git_calls(calls, "add"), "add trotz Pfad außerhalb Repo"
    assert not _git_calls(calls, "commit")


def test_archive_git_timeout_does_not_raise(tmp_path, monkeypatch):
    import subprocess as _sp
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)

    def timeout_run(cmd, **kwargs):
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 30))

    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", timeout_run)
    _archive_release_notes(ctx)  # kein Crash trotz git-Timeout
    assert (tmp_path / "docs" / "release-history" / "v1.2.3.md").exists()


def test_archive_invalid_version_skips_entirely(tmp_path, monkeypatch):
    # Pfad-Traversal/Newline in der Version → gar nicht archivieren, kein git.
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path, version="1.2.3/../../etc/passwd")
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert calls == [], "git lief trotz ungültiger Version"
    assert not (tmp_path / "docs" / "release-history").exists(), "Archiv-Dir trotz ungültiger Version"


def test_archive_newline_version_skips(tmp_path, monkeypatch):
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path, version="1.2.3\nmalicious")
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    _archive_release_notes(ctx)
    assert calls == []


def test_archive_push_error_stderr_is_sanitized(tmp_path, monkeypatch, caplog):
    # HIGH-Fix: git-stderr mit Token/URL darf NICHT im Klartext geloggt werden.
    import logging
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    leak = "remote: https://x-access-token:ghp_SECRET12345@github.com/o/r.git\nfatal: auth failed"
    fake_run, _ = _git_responder(overrides={"push": (1, "", leak)})
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    with caplog.at_level(logging.WARNING, logger="shadowops"):
        _archive_release_notes(ctx)

    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "ghp_SECRET12345" not in msgs, "Token im Log geleakt!"
    assert "x-access-token" not in msgs, "Auth-URL im Log geleakt!"
    assert "github.com" not in msgs


def test_archive_successful_push_logs_info(tmp_path, monkeypatch, caplog):
    import logging
    _seed_release_notes(tmp_path)
    ctx = _archive_ctx(tmp_path)
    fake_run, calls = _git_responder()
    monkeypatch.setattr("patch_notes.stages.distribute.subprocess.run", fake_run)

    with caplog.at_level(logging.INFO, logger="shadowops"):
        _archive_release_notes(ctx)

    assert _git_calls(calls, "push"), "Push fehlt im Happy-Path"
    assert any("committed + gepusht" in r.getMessage() for r in caplog.records)
