"""Tests fuer SEO-Prompt-Builder."""
import pytest

from src.integrations.github_integration.agent_review.prompts.seo_prompt import (
    build_seo_review_prompt,
    truncate_diff,
)


def test_prompt_has_role_and_project():
    p = build_seo_review_prompt(
        diff="diff --git a/x b/x", project="ZERODOX", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    assert "Senior SEO-Reviewer" in p
    assert "ZERODOX" in p
    assert "Iteration: 1" in p


def test_prompt_mentions_all_4_domains():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    # Multi-Domain SEO + GSC + GEO + AEO
    assert "SEO" in p
    assert "GSC" in p or "Search Console" in p
    assert "GEO" in p or "Local" in p
    assert "AEO" in p or "Answer Engine" in p or "AI Search" in p


def test_prompt_includes_scope_check_instructions():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    assert "Scope" in p
    assert "package.json" in p or "Build-Config" in p
    assert "scope_check" in p


def test_prompt_includes_files_list():
    files = ["web/src/content/blog-1.md", "web/src/lib/sitemap.ts"]
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=files, knowledge=[], few_shot=[],
    )
    for f in files:
        assert f in p


def test_prompt_handles_empty_files_list():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    # Should not crash, should have placeholder text
    assert p


def test_prompt_includes_knowledge():
    p = build_seo_review_prompt(
        diff="", project="ZERODOX", iteration=1,
        files_changed=[],
        knowledge=["ZERODOX nutzt Prisma — Schema-Aenderungen brauchen migrate"],
        few_shot=[],
    )
    assert "Prisma" in p


def test_prompt_includes_few_shot_examples():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[],
        knowledge=[],
        few_shot=[
            {"outcome": "good_catch", "diff_summary": "Defu removal in scope"},
            {"outcome": "approved_clean", "diff_summary": "Simple meta update"},
        ],
    )
    assert "good_catch" in p
    assert "approved_clean" in p


def test_prompt_mentions_severity_levels():
    """severity enum from jules_review.json schema (incl. 'low' added 2026-04-14)."""
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    assert "critical" in p
    assert "high" in p
    assert "medium" in p
    assert "low" in p


def test_prompt_specifies_50_files_blocker():
    """Bei >50 File-Aenderungen muss Claude BLOCKER setzen."""
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    assert "50" in p


def test_truncate_diff_short_unchanged():
    short = "diff --git a/x b/x\n+test"
    assert truncate_diff(short, max_chars=8000) == short


def test_truncate_diff_long_truncated():
    long = "x" * 20000
    out = truncate_diff(long, max_chars=500)
    assert len(out) < 1000  # truncated + marker
    assert "abgeschnitten" in out or "truncated" in out.lower()


def test_truncate_diff_default_max_chars():
    long = "x" * 12000
    out = truncate_diff(long)
    # Default max_chars=8000 — output should be ≤ 8000 + marker length
    assert len(out) < 8500


def test_prompt_truncates_long_diff():
    long_diff = "x" * 50000
    p = build_seo_review_prompt(
        diff=long_diff, project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
        max_diff_chars=500,
    )
    # Total prompt should not be huge, diff is truncated
    assert len(p) < 15000


def test_prompt_returns_string():
    p = build_seo_review_prompt(
        diff="", project="X", iteration=1,
        files_changed=[], knowledge=[], few_shot=[],
    )
    assert isinstance(p, str)
    assert len(p) > 100  # non-trivial
