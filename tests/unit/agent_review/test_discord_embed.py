"""Tests fuer Review-Embed-Formatter."""
import pytest

from src.integrations.github_integration.agent_review.discord_embed import (
    build_review_embed, pick_color,
    COLOR_AUTO_MERGED, COLOR_APPROVED, COLOR_REVISION, COLOR_ESCALATED, COLOR_ERROR,
)


# ─────────── pick_color ───────────

class TestPickColor:
    def test_approved_manual(self):
        assert pick_color(verdict="approved", auto_merged=False) == COLOR_APPROVED

    def test_approved_auto_merged(self):
        assert pick_color(verdict="approved", auto_merged=True) == COLOR_AUTO_MERGED

    def test_revision(self):
        assert pick_color(verdict="revision_requested") == COLOR_REVISION

    def test_escalated_overrides_verdict(self):
        assert pick_color(verdict="approved", auto_merged=True, escalated=True) == COLOR_ESCALATED
        assert pick_color(verdict="revision_requested", escalated=True) == COLOR_ESCALATED

    def test_error(self):
        assert pick_color(verdict="error") == COLOR_ERROR

    def test_unknown_defaults_approved_blue(self):
        assert pick_color(verdict="something_else") == COLOR_APPROVED


# ─────────── build_review_embed ───────────

class TestBuildReviewEmbed:
    def _minimal_review(self):
        return {
            "verdict": "approved",
            "summary": "Solid fix with tests.",
            "blockers": [],
            "suggestions": [],
            "nits": [],
        }

    def test_approved_pending_manual_merge(self):
        e = build_review_embed(
            agent_name="jules", repo="Commandershadow9/ZERODOX",
            pr_number=42, pr_url="https://github.com/x/y/pull/42",
            review=self._minimal_review(),
            iteration=1, max_iterations=5,
            auto_merged=False,
        )
        assert e.color.value == COLOR_APPROVED
        assert "JULES" in e.title
        assert "#42" in e.title
        assert "APPROVED" in e.title
        assert "auto-merged" not in e.title

    def test_approved_auto_merged(self):
        e = build_review_embed(
            agent_name="seo", repo="Commandershadow9/ZERODOX",
            pr_number=5, pr_url="https://github.com/x/y/pull/5",
            review=self._minimal_review(),
            iteration=1, max_iterations=5,
            auto_merged=True,
        )
        assert e.color.value == COLOR_AUTO_MERGED
        assert "auto-merged" in e.title
        assert "SEO" in e.title

    def test_revision_yellow(self):
        rev = {
            "verdict": "revision_requested",
            "summary": "Missing tests for new util.",
            "blockers": [{"title": "No test", "reason": "missing", "severity": "high"}],
            "suggestions": [], "nits": [],
        }
        e = build_review_embed(
            agent_name="codex", repo="a/b", pr_number=10,
            pr_url="https://github.com/a/b/pull/10",
            review=rev, iteration=2, max_iterations=5,
        )
        assert e.color.value == COLOR_REVISION
        assert "REVISION_REQUESTED" in e.title

    def test_escalated_orange(self):
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://github.com/a/b/pull/1",
            review=self._minimal_review(),
            iteration=6, max_iterations=5, escalated=True,
        )
        assert e.color.value == COLOR_ESCALATED
        assert "⚠️" in e.title

    def test_fields_basic(self):
        e = build_review_embed(
            agent_name="jules", repo="Commandershadow9/test",
            pr_number=99, pr_url="https://github.com/x/y/pull/99",
            review=self._minimal_review(),
            iteration=1, max_iterations=5,
        )
        field_names = [f.name for f in e.fields]
        assert "Repo" in field_names
        assert "Iteration" in field_names
        assert "Findings" in field_names
        assert "Summary" in field_names

    def test_iteration_field_format(self):
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=self._minimal_review(),
            iteration=3, max_iterations=5,
        )
        iter_field = next(f for f in e.fields if f.name == "Iteration")
        assert iter_field.value == "3/5"

    def test_findings_count_format(self):
        rev = {
            "verdict": "revision_requested",
            "summary": "",
            "blockers": [{"title": "b1"}, {"title": "b2"}],
            "suggestions": [{"title": "s1"}, {"title": "s2"}, {"title": "s3"}],
            "nits": [{"title": "n1"}],
        }
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=rev, iteration=1, max_iterations=5,
        )
        findings_field = next(f for f in e.fields if f.name == "Findings")
        assert "🔴 2" in findings_field.value
        assert "🟡 3" in findings_field.value
        assert "⚪ 1" in findings_field.value

    def test_blockers_rendered_when_present(self):
        rev = {
            "verdict": "revision_requested",
            "summary": "",
            "blockers": [
                {"title": "SQL-Injection", "severity": "critical"},
                {"title": "XSS", "severity": "high"},
            ],
            "suggestions": [], "nits": [],
        }
        e = build_review_embed(
            agent_name="codex", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=rev, iteration=1, max_iterations=5,
        )
        blocker_field = next(f for f in e.fields if f.name == "Blockers")
        assert "SQL-Injection" in blocker_field.value
        assert "XSS" in blocker_field.value
        assert "[critical]" in blocker_field.value

    def test_blockers_capped_at_three(self):
        rev = {
            "verdict": "revision_requested",
            "summary": "",
            "blockers": [{"title": f"Block{i}", "severity": "high"} for i in range(7)],
            "suggestions": [], "nits": [],
        }
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=rev, iteration=1, max_iterations=5,
        )
        blocker_field = next(f for f in e.fields if f.name == "Blockers")
        assert "Block0" in blocker_field.value
        assert "Block2" in blocker_field.value
        assert "Block3" not in blocker_field.value
        assert "+4 weitere" in blocker_field.value

    def test_empty_summary_skips_field(self):
        rev = self._minimal_review()
        rev["summary"] = ""
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=rev, iteration=1, max_iterations=5,
        )
        field_names = [f.name for f in e.fields]
        assert "Summary" not in field_names

    def test_no_blockers_skips_field(self):
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=self._minimal_review(),
            iteration=1, max_iterations=5,
        )
        field_names = [f.name for f in e.fields]
        assert "Blockers" not in field_names

    def test_model_used_in_footer(self):
        e = build_review_embed(
            agent_name="jules", repo="a/b", pr_number=1,
            pr_url="https://x/y", review=self._minimal_review(),
            iteration=1, max_iterations=5, model_used="claude-opus-4-6",
        )
        assert "claude-opus-4-6" in e.footer.text
        assert "ShadowOps SecOps" in e.footer.text
