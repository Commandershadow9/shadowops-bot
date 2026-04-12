import pytest
from src.integrations.github_integration.jules_review_prompt import (
    compute_verdict,
    build_review_prompt,
    truncate_diff,
)


def _base_review():
    return {
        "verdict": "approved",
        "summary": "x",
        "blockers": [],
        "suggestions": [],
        "nits": [],
        "scope_check": {"in_scope": True, "explanation": "x"},
    }


def test_compute_verdict_empty_blockers_in_scope_approved():
    assert compute_verdict(_base_review()) == "approved"


def test_compute_verdict_with_blockers_revision():
    r = _base_review()
    r["blockers"] = [{"title": "x", "reason": "y", "file": "z", "severity": "high"}]
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_out_of_scope_revision():
    r = _base_review()
    r["scope_check"]["in_scope"] = False
    assert compute_verdict(r) == "revision_requested"


def test_compute_verdict_ignores_suggestions_and_nits():
    r = _base_review()
    r["suggestions"] = [{"title": "s", "reason": "r", "file": "f", "severity": "medium"}]
    r["nits"] = [{"title": "n", "reason": "r", "file": "f", "severity": "medium"}]
    assert compute_verdict(r) == "approved"


def test_build_review_prompt_contains_all_blocks():
    finding = {
        "title": "ReDoS in picomatch",
        "severity": "high",
        "category": "npm_audit",
        "cve": "CVE-2024-45296",
        "description": "Vulnerable regex in picomatch <4.0.4",
    }
    prompt = build_review_prompt(
        finding=finding,
        project="ZERODOX",
        diff="diff --git a/x b/x\n+new line\n",
        iteration=2,
        project_knowledge=["ZERODOX nutzt Prisma"],
        few_shot_examples=[{
            "outcome": "good_catch",
            "diff_summary": "Dep bump mit Drive-by removal",
            "review_json": {"verdict": "revision_requested", "blockers": [{"x": 1}]},
        }],
    )
    assert "ReDoS in picomatch" in prompt
    assert "CVE-2024-45296" in prompt
    assert "**Iteration:** 2 of 5" in prompt
    assert "Prisma" in prompt
    assert "good_catch" in prompt
    assert "diff --git" in prompt


def test_truncate_diff_cuts_and_marks():
    long = "x" * 10000
    out = truncate_diff(long, max_chars=100)
    assert len(out) < 200
    assert "abgeschnitten" in out


def test_truncate_diff_short_unchanged():
    assert truncate_diff("abc") == "abc"
