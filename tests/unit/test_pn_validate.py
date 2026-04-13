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
async def test_validate_none_result():
    ctx = _make_ctx(ai_result=None)
    await validate(ctx)
    assert "Kein AI-Result" in ctx.warnings[0]
