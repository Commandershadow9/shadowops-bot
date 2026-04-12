from src.integrations.github_integration.jules_comment import (
    build_review_comment_body, is_bot_comment, COMMENT_MARKER,
)


def _review(verdict="approved", blockers=None, suggestions=None, nits=None, in_scope=True):
    return {
        "verdict": verdict, "summary": "Test summary",
        "blockers": blockers or [], "suggestions": suggestions or [],
        "nits": nits or [],
        "scope_check": {"in_scope": in_scope, "explanation": "exp"},
    }


def test_approved_has_green_marker():
    body = build_review_comment_body(review=_review(), iteration=1, pr_number=123, finding_id=42)
    assert "APPROVED" in body.upper()
    assert "Iteration 1 of 5" in body
    assert "PR #123" in body
    assert "Finding #42" in body


def test_revision_lists_blockers():
    blockers = [{"title": "Scope violation", "reason": "defu removed",
                 "file": "web/package.json", "line": 23, "severity": "high",
                 "suggested_fix": "Revert"}]
    body = build_review_comment_body(
        review=_review(verdict="revision_requested", blockers=blockers, in_scope=False),
        iteration=2, pr_number=123, finding_id=42)
    assert "REVISION" in body.upper()
    assert "Scope violation" in body
    assert "web/package.json" in body


def test_suggestions_not_blocking():
    body = build_review_comment_body(
        review=_review(suggestions=[{"title": "Dedup", "reason": "nicer", "file": "x",
                                     "severity": "medium", "suggested_fix": "npm dedupe"}]),
        iteration=1, pr_number=1, finding_id=1)
    assert "Dedup" in body
    assert "APPROVED" in body.upper()


def test_marker_prefix_for_self_filter():
    body = build_review_comment_body(review=_review(), iteration=1, pr_number=1, finding_id=1)
    assert body.startswith(COMMENT_MARKER)


def test_is_bot_comment_true():
    assert is_bot_comment(f"{COMMENT_MARKER} — Iteration 1 of 5\nstuff") is True


def test_is_bot_comment_false():
    assert is_bot_comment("Looks good to me!") is False


def test_empty_review_shows_no_annotations():
    body = build_review_comment_body(review=_review(), iteration=1, pr_number=1, finding_id=1)
    assert "Keine Anmerkungen" in body
