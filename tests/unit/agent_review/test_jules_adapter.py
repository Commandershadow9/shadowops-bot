"""Tests fuer JulesAdapter."""
import pytest

from src.integrations.github_integration.agent_review.adapters.jules import (
    JulesAdapter,
    SECURITY_KEYWORDS,
)
from src.integrations.github_integration.agent_review.adapters.base import (
    MergeDecision,
)


@pytest.fixture
def adapter():
    return JulesAdapter()


# ──────── detect() ────────

class TestDetect:
    def test_label_match_full_confidence(self, adapter):
        pr = {
            "labels": [{"name": "jules"}, {"name": "security"}],
            "user": {"login": "Commandershadow9"},
            "body": "",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 1.0
        assert d.metadata["src"] == "label"

    def test_bot_author_match(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "google-labs-jules[bot]"},
            "body": "",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 1.0
        assert d.metadata["src"] == "bot_author"

    def test_body_marker_pr_created(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "Commandershadow9"},
            "body": "Some text\n\n---\n*PR created automatically by Jules for task #42*",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.9
        assert d.metadata["src"] == "body_marker"

    def test_body_marker_url(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "Commandershadow9"},
            "body": "Fixes issue X\n\nSee jules.google.com/task/123 for details",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.85

    def test_no_match_for_seo_pr(self, adapter):
        pr = {
            "labels": [{"name": "seo"}],
            "user": {"login": "Commandershadow9"},
            "body": "## 🔍 SEO Audit",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_no_match_for_dependabot(self, adapter):
        pr = {
            "labels": [{"name": "dependencies"}],
            "user": {"login": "dependabot[bot]"},
            "body": "Bumps the npm-major group",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_label_priority_over_body(self, adapter):
        # Wenn Label vorhanden, sollte das die hoechste Confidence geben
        pr = {
            "labels": [{"name": "jules"}],
            "user": {"login": "Commandershadow9"},
            "body": "*PR created automatically by Jules*",
        }
        d = adapter.detect(pr)
        assert d.confidence == 1.0  # Label wins, nicht 0.9 vom Body

    def test_label_case_insensitive(self, adapter):
        pr = {"labels": [{"name": "JULES"}], "user": {"login": "x"}, "body": ""}
        d = adapter.detect(pr)
        assert d.matched is True


# ──────── model_preference() ────────

class TestModelPreference:
    def test_security_keyword_uses_thinking(self, adapter):
        pr = {"title": "Fix XSS in blog renderer"}
        primary, fallback = adapter.model_preference(pr, diff_len=500)
        assert primary == "thinking"
        assert fallback == "standard"

    def test_cve_keyword_uses_thinking(self, adapter):
        pr = {"title": "Update Next.js for CVE-2024-...."}
        primary, fallback = adapter.model_preference(pr, diff_len=200)
        assert primary == "thinking"

    def test_trivial_uses_standard(self, adapter):
        pr = {"title": "Replace console.log with logger"}
        primary, fallback = adapter.model_preference(pr, diff_len=200)
        assert primary == "standard"
        assert fallback == "thinking"

    def test_large_diff_uses_thinking(self, adapter):
        pr = {"title": "Refactor components"}
        primary, _ = adapter.model_preference(pr, diff_len=5000)
        assert primary == "thinking"

    def test_security_keywords_detected_case_insensitive(self, adapter):
        pr = {"title": "Fix CSRF Vulnerability"}
        primary, _ = adapter.model_preference(pr, diff_len=100)
        assert primary == "thinking"


# ──────── merge_policy() ────────

class TestMergePolicy:
    def _approved_review(self):
        return {"verdict": "approved"}

    def test_blocked_when_project_frozen(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "Tests",
            "additions": 50,
            "files_changed_paths": ["tests/test_x.py"],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "sicherheitsdienst") == MergeDecision.MANUAL

    def test_manual_when_not_approved(self, adapter):
        review = {"verdict": "revision_requested"}
        pr = {
            "title": "Tests",
            "additions": 50,
            "files_changed_paths": ["tests/test_x.py"],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_for_security_label(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "Update deps",
            "additions": 50,
            "files_changed_paths": ["tests/test_x.py"],
            "labels": [{"name": "security"}],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_for_security_keyword_in_title(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "Fix XSS vulnerability",
            "additions": 50,
            "files_changed_paths": ["tests/test_x.py"],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_auto_for_tests_only_small(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "Add unit tests",
            "additions": 150,
            "files_changed_paths": [
                "tests/unit/test_foo.py",
                "tests/unit/test_bar.py",
            ],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.AUTO

    def test_manual_for_tests_only_too_large(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "Add many tests",
            "additions": 500,    # > 200 threshold
            "files_changed_paths": ["tests/unit/test_x.py"],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_for_mixed_paths(self, adapter):
        # Tests + Prod-Code = nicht auto
        review = self._approved_review()
        pr = {
            "title": "Add tests",
            "additions": 100,
            "files_changed_paths": [
                "tests/unit/test_x.py",
                "src/foo.py",  # Prod-Code dabei
            ],
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_no_paths_known(self, adapter):
        review = self._approved_review()
        pr = {
            "title": "x",
            "additions": 50,
            "files_changed_paths": [],  # leer
            "labels": [],
        }
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL


# ──────── discord_channel + iteration_mention ────────

class TestDiscordAndMention:
    def test_discord_channel_constant(self, adapter):
        assert adapter.discord_channel("approved") == "🔧-code-fixes"
        assert adapter.discord_channel("revision_requested") == "🔧-code-fixes"

    def test_iteration_mention_jules(self, adapter):
        assert adapter.iteration_mention() == "@google-labs-jules"


# ──────── agent_name ────────

def test_agent_name(adapter):
    assert adapter.agent_name == "jules"


# ──────── SECURITY_KEYWORDS export ────────

def test_security_keywords_complete():
    """Sicherstellen dass die wichtigsten Keywords enthalten sind."""
    expected = {"xss", "cve", "injection", "dos", "security", "auth", "csrf", "rce"}
    assert set(SECURITY_KEYWORDS) == expected
