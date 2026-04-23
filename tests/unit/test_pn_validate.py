"""Tests für Stufe 4: Validate — Safety-Checks."""
import pytest
from patch_notes.stages.validate import (
    check_feature_count, check_design_doc_leaks, strip_ai_version,
    normalize_umlauts, extract_display_content, validate,
)
from patch_notes.context import PipelineContext


def _make_ctx(**kwargs) -> PipelineContext:
    defaults = dict(
        project="test", project_config={"patch_notes": {"language": "de"}},
        raw_commits=[], trigger="manual",
    )
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def test_strip_ai_version_removes_semver():
    ctx = _make_ctx(ai_result={"title": "v0.21.0 - Content-Explosion & Funk-Upgrade"})
    strip_ai_version(ctx)
    assert ctx.ai_result["title"] == "Content-Explosion & Funk-Upgrade"


def test_strip_ai_version_no_version():
    ctx = _make_ctx(ai_result={"title": "Rollen-System & Spielstand-Verwaltung"})
    strip_ai_version(ctx)
    assert ctx.ai_result["title"] == "Rollen-System & Spielstand-Verwaltung"


def test_strip_ai_version_only_version():
    ctx = _make_ctx(ai_result={"title": "v1.0.0"})
    strip_ai_version(ctx)
    assert ctx.ai_result["title"] == "Update"


def test_feature_count_warns():
    ctx = _make_ctx(
        ai_result={"changes": [{"type": "feature"}] * 20},
        groups=[{"tag": "FEATURE"}],
    )
    check_feature_count(ctx)
    assert len(ctx.warnings) > 0


def test_feature_count_ok():
    ctx = _make_ctx(
        ai_result={"changes": [{"type": "feature"}] * 3},
        groups=[{"tag": "FEATURE"}, {"tag": "FEATURE"}],
    )
    check_feature_count(ctx)
    assert len(ctx.warnings) == 0


def test_design_doc_leak_removal():
    ctx = _make_ctx(
        ai_result={"changes": [
            {"type": "feature", "description": "Neues Referral-System implementiert"},
            {"type": "feature", "description": "Login verbessert"},
        ]},
        groups=[
            {"tag": "DESIGN_DOC", "theme": "Referral System Design", "summary": ""},
            {"tag": "FEATURE", "theme": "Login", "summary": "login verbessert"},
        ],
    )
    check_design_doc_leaks(ctx)
    assert len(ctx.ai_result["changes"]) == 1
    assert "Login" in ctx.ai_result["changes"][0]["description"]


def test_design_doc_false_positive_protection():
    """Wenn Keyword sowohl in Design-Doc als auch in Feature-Commits: NICHT entfernen."""
    ctx = _make_ctx(
        ai_result={"changes": [
            {"type": "feature", "description": "Rollen-System implementiert"},
        ]},
        groups=[
            {"tag": "DESIGN_DOC", "theme": "Rollen System Design", "summary": ""},
            {"tag": "FEATURE", "theme": "Rollen System", "summary": "rollen system"},
        ],
    )
    check_design_doc_leaks(ctx)
    assert len(ctx.ai_result["changes"]) == 1  # NICHT entfernt


def test_extract_structured():
    ctx = _make_ctx(ai_result={
        "title": "Big Update", "tldr": "Viel Neues",
        "web_content": "Langer Text", "changes": [{"type": "feature"}],
        "seo_keywords": ["test"],
    })
    extract_display_content(ctx)
    assert ctx.title == "Big Update"
    assert ctx.tldr == "Viel Neues"
    assert len(ctx.changes) == 1


def test_extract_raw_string():
    ctx = _make_ctx(ai_result="Einfacher Text mit Updates")
    extract_display_content(ctx)
    assert ctx.title == "test Update"
    assert "Einfacher Text" in ctx.web_content


@pytest.mark.asyncio
async def test_validate_full_pipeline():
    ctx = _make_ctx(
        ai_result={
            "title": "v2.0.0 - Super Update",
            "tldr": "Tolles Update",
            "web_content": "Content",
            "changes": [{"type": "feature", "description": "Neues Feature"}],
        },
        groups=[{"tag": "FEATURE", "theme": "Test", "summary": ""}],
        version="1.5.0",
    )
    await validate(ctx)
    assert ctx.title == "Super Update"  # Version entfernt
    assert ctx.tldr == "Tolles Update"


@pytest.mark.asyncio
async def test_validate_none_result_raises():
    """Bei ai_result=None MUSS Pipeline abbrechen — keine leeren DB-Einträge."""
    ctx = _make_ctx(ai_result=None)
    with pytest.raises(RuntimeError, match="None"):
        await validate(ctx)


@pytest.mark.asyncio
async def test_validate_empty_dict_raises():
    """Bei leerem dict (kein title/tldr/content) — Pipeline bricht ab."""
    ctx = _make_ctx(ai_result={"changes": []})
    with pytest.raises(RuntimeError, match="leer"):
        await validate(ctx)


@pytest.mark.asyncio
async def test_validate_empty_string_raises():
    """Bei leerem String-Result — Pipeline bricht ab."""
    ctx = _make_ctx(ai_result="   ")
    with pytest.raises(RuntimeError, match="leer"):
        await validate(ctx)


# ── Multi-Author Enrichment (2026-04-15) ───────────────────────


def test_enrich_multi_author_primary_plus_coauthors():
    """Zwei Autoren mit ausreichendem Overlap -> beide in change.authors, Primary zuerst."""
    from patch_notes.stages.validate import enrich_changes_with_authors

    ctx = _make_ctx(
        enriched_commits=[
            {"message": "feat: kaskaden einsatz phase state machine", "author": {"name": "cmdshadow"}},
            {"message": "feat: kaskaden timer phase", "author": {"name": "cmdshadow"}},
            {"message": "feat: einsatz phase rueckzuendung", "author": {"name": "cmdshadow"}},
            {"message": "feat: kaskaden marker phase", "author": {"name": "renjihoshida"}},
            {"message": "feat: einsatz phase transition", "author": {"name": "renjihoshida"}},
        ],
        changes=[
            {"type": "feature", "description": "einsatz kaskaden phase",
             "details": ["rueckzuendung per timer", "marker transition"]},
        ],
    )
    enrich_changes_with_authors(ctx)
    authors = ctx.changes[0].get('authors') or []
    assert len(authors) == 2, f"Expected 2 authors, got: {authors}"
    assert authors[0] == "Shadow"  # Primary (hoechster Overlap)
    assert "Mapu" in authors
    assert ctx.changes[0].get('author') == "Shadow"  # Backward-Compat


def test_enrich_single_author_fills_list():
    """Nur ein Author mit Overlap -> authors=[Primary]."""
    from patch_notes.stages.validate import enrich_changes_with_authors

    ctx = _make_ctx(
        enriched_commits=[
            {"message": "feat: transport klinik marker", "author": {"name": "cmdshadow"}},
            {"message": "feat: transport uebergabe timer", "author": {"name": "cmdshadow"}},
        ],
        changes=[
            {"type": "feature", "description": "transport klinik", "details": ["marker timer"]},
        ],
    )
    enrich_changes_with_authors(ctx)
    assert ctx.changes[0].get('author') == "Shadow"
    assert ctx.changes[0].get('authors') == ["Shadow"]


def test_enrich_dedups_aliases():
    """Zwei git-Aliases desselben Menschen -> nur EINMAL im authors-Array."""
    from patch_notes.stages.validate import enrich_changes_with_authors

    ctx = _make_ctx(
        enriched_commits=[
            {"message": "feat: kaskaden phase state", "author": {"name": "cmdshadow"}},
            {"message": "feat: kaskaden timer state", "author": {"name": "commandershadow9"}},
        ],
        changes=[
            {"type": "feature", "description": "kaskaden phase", "details": ["timer state"]},
        ],
    )
    enrich_changes_with_authors(ctx)
    authors = ctx.changes[0].get('authors') or []
    assert authors.count("Shadow") == 1
