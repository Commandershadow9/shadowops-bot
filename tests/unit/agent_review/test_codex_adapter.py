"""Tests fuer CodexAdapter + Codex-Prompt."""
import pytest

from src.integrations.github_integration.agent_review.adapters.codex import (
    CodexAdapter,
    SECURITY_BRANCH_PREFIX,
    BODY_MARKERS,
)
from src.integrations.github_integration.agent_review.adapters.base import (
    MergeDecision,
)
from src.integrations.github_integration.agent_review.prompts.codex_prompt import (
    build_codex_review_prompt,
    truncate_diff,
    MAX_DIFF_CHARS_DEFAULT,
)


@pytest.fixture
def adapter():
    return CodexAdapter()


# ─────────── detect() ───────────

class TestDetect:
    def test_canonical_security_branch_full_confidence(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "fix/security-findings"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 1.0

    def test_security_branch_with_suffix(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "fix/security-findings-2026-04-14"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.confidence == 1.0

    def test_generic_security_branch(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "fix/security-cve-2026-1234"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.95

    def test_codex_branch_prefix(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "codex/refactor-auth"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.9

    def test_body_marker_codex(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"},
            "body": "Co-Authored-By: Codex <noreply@openai.com>",
            "head": {"ref": "main"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.9

    def test_body_marker_security_fix(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"},
            "body": "## 🔧 Security Fix\n\nFinding: ...",
            "head": {"ref": "main"}, "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True

    def test_title_security_prefix(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "main"}, "title": "[Security] Fix XSS in user-input",
        }
        d = adapter.detect(pr)
        assert d.confidence == 0.85

    def test_title_codex_prefix(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"}, "body": "",
            "head": {"ref": "main"}, "title": "[Codex] Refactor logger setup",
        }
        d = adapter.detect(pr)
        assert d.confidence == 0.85

    def test_no_match_for_jules(self, adapter):
        pr = {
            "labels": [{"name": "jules"}],
            "user": {"login": "google-labs-jules[bot]"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "fix/auth"},  # nicht security/-codex
            "title": "Fix auth",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_no_match_for_seo(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"},
            "body": "## 🔍 SEO Audit",
            "head": {"ref": "seo/zerodox"},
            "title": "[SEO] Optimierung",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_no_match_for_random_pr(self, adapter):
        pr = {
            "labels": [], "user": {"login": "x"},
            "body": "Just a normal PR",
            "head": {"ref": "feature/new-button"},
            "title": "Add button",
        }
        d = adapter.detect(pr)
        assert d.matched is False


# ─────────── model_preference() ───────────

class TestModelPreference:
    def test_security_branch_always_opus(self, adapter):
        pr = {"head": {"ref": "fix/security-findings"}, "title": "x"}
        primary, fallback = adapter.model_preference(pr, diff_len=500)
        assert primary == "thinking"
        assert fallback == "standard"

    def test_security_title_opus(self, adapter):
        pr = {"head": {"ref": "main"}, "title": "[Security] CVE-fix"}
        primary, _ = adapter.model_preference(pr, diff_len=100)
        assert primary == "thinking"

    def test_small_refactor_sonnet(self, adapter):
        pr = {"head": {"ref": "codex/cleanup"}, "title": "[Codex] cleanup"}
        primary, fallback = adapter.model_preference(pr, diff_len=500)
        assert primary == "standard"
        assert fallback == "thinking"

    def test_large_refactor_opus(self, adapter):
        pr = {"head": {"ref": "codex/big-refactor"}, "title": "x"}
        primary, _ = adapter.model_preference(pr, diff_len=5000)
        assert primary == "thinking"


# ─────────── merge_policy() ───────────

class TestMergePolicy:
    def test_always_manual_even_when_approved(self, adapter):
        review = {"verdict": "approved", "scope_check": {"in_scope": True}}
        pr = {"files_changed_paths": ["src/auth.py"], "additions": 20}
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_revision_requested(self, adapter):
        review = {"verdict": "revision_requested", "scope_check": {"in_scope": True}}
        pr = {"files_changed_paths": ["src/auth.py"], "additions": 5}
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


# ─────────── discord_channel + iteration_mention ───────────

class TestDiscordAndMention:
    def test_discord_channel(self, adapter):
        assert adapter.discord_channel("approved") == "security-fixes"
        assert adapter.discord_channel("revision_requested") == "security-fixes"

    def test_no_iteration_mention(self, adapter):
        assert adapter.iteration_mention() is None


# ─────────── Constants & agent_name ───────────

class TestConstants:
    def test_agent_name(self, adapter):
        assert adapter.agent_name == "codex"

    def test_security_branch_constant(self):
        assert SECURITY_BRANCH_PREFIX == "fix/security-findings"

    def test_body_markers_present(self):
        assert any("Codex" in m for m in BODY_MARKERS)
        assert any("Security" in m for m in BODY_MARKERS)


# ─────────── Codex-Prompt ───────────

class TestCodexPrompt:
    def test_prompt_contains_role(self):
        p = build_codex_review_prompt(
            diff="--- a/x\n+++ b/x\n+pass",
            project="ZERODOX", iteration=1,
            files_changed=["src/x.py"],
            knowledge=[], few_shot=[],
        )
        assert "Senior Code-Reviewer" in p
        assert "ZERODOX" in p
        assert "Iteration: 1" in p

    def test_prompt_includes_files(self):
        p = build_codex_review_prompt(
            diff="diff",
            project="X", iteration=1,
            files_changed=["src/auth.py", "tests/test_auth.py"],
            knowledge=[], few_shot=[],
        )
        assert "src/auth.py" in p
        assert "tests/test_auth.py" in p

    def test_prompt_includes_finding_context_when_present(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=1,
            files_changed=[], knowledge=[], few_shot=[],
            finding_context={
                "title": "XSS in user-input",
                "severity": "high",
                "description": "User input gets rendered as raw HTML",
            },
        )
        assert "XSS in user-input" in p
        assert "high" in p
        assert "rendered as raw HTML" in p

    def test_prompt_omits_finding_context_when_none(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=1,
            files_changed=[], knowledge=[], few_shot=[],
            finding_context=None,
        )
        assert "Urspruengliches Security-Finding" not in p

    def test_prompt_includes_knowledge(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=1,
            files_changed=[],
            knowledge=["ZERODOX nutzt Prisma fuer DB-Queries"],
            few_shot=[],
        )
        assert "Prisma" in p

    def test_prompt_format_examples(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=2,
            files_changed=[], knowledge=[],
            few_shot=[
                {"outcome": "approved", "diff_summary": "fixed null check"},
                {"outcome": "revision_requested", "diff_summary": "missing test"},
            ],
        )
        assert "[approved]" in p
        assert "[revision_requested]" in p

    def test_prompt_strict_security_language(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=1,
            files_changed=[], knowledge=[], few_shot=[],
        )
        assert "wasserdicht" in p
        assert "SQL-Injection" in p
        assert "Command-Injection" in p

    def test_prompt_severity_levels(self):
        p = build_codex_review_prompt(
            diff="x", project="X", iteration=1,
            files_changed=[], knowledge=[], few_shot=[],
        )
        assert "critical|high|medium|low" in p


class TestTruncateDiff:
    def test_short_diff_unchanged(self):
        d = "short"
        assert truncate_diff(d) == d

    def test_long_diff_truncated(self):
        d = "x" * (MAX_DIFF_CHARS_DEFAULT + 500)
        out = truncate_diff(d)
        assert len(out) < len(d)
        assert "abgeschnitten" in out

    def test_custom_max_chars(self):
        d = "x" * 100
        out = truncate_diff(d, max_chars=50)
        assert len(out) < 100
        assert "50 Zeichen abgeschnitten" in out
