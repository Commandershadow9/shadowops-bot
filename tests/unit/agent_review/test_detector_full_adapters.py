"""Integration-Tests fuer AgentDetector mit allen 3 Adaptern.

Stellt sicher, dass:
- Jeder Adapter seine eigenen PRs erkennt
- Kein Adapter fremde PRs abfaengt (Confidence-Trennung)
- Bei Ambiguitaet gewinnt der Adapter mit hoechster Confidence
"""
import pytest

from src.integrations.github_integration.agent_review.detector import AgentDetector
from src.integrations.github_integration.agent_review.adapters.jules import JulesAdapter
from src.integrations.github_integration.agent_review.adapters.seo import SeoAdapter
from src.integrations.github_integration.agent_review.adapters.codex import CodexAdapter


@pytest.fixture
def detector_all():
    """Detector mit allen 3 Adaptern aktiv."""
    return AgentDetector([JulesAdapter(), SeoAdapter(), CodexAdapter()])


# ─────────── Real-World PR-Samples ───────────

class TestJulesPr:
    def test_jules_pr_with_label(self, detector_all):
        pr = {
            "labels": [{"name": "jules"}],
            "user": {"login": "Commandershadow9"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "jules/fix-issue-123"},
            "title": "Fix XSS in user input",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "jules"

    def test_jules_pr_body_marker_only(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "Commandershadow9"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "fix/auth"},
            "title": "Fix auth bug",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "jules"


class TestSeoPr:
    def test_seo_audit_body(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "## 🔍 SEO Audit — Automatische Fixes\n\nSite: zerodox.de",
            "head": {"ref": "seo/zerodox/2026-04-14"},
            "title": "[SEO] Blog-Updates",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "seo"

    def test_seo_title_prefix(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "main"},
            "title": "[SEO] neue Meta-Descriptions",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "seo"


class TestCodexPr:
    def test_security_findings_branch(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "Fixes finding #42",
            "head": {"ref": "fix/security-findings"},
            "title": "fix: SQL Injection in query builder",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "codex"

    def test_codex_branch(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "codex/refactor-logger"},
            "title": "refactor: logger setup",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "codex"


# ─────────── Disambiguation ───────────

class TestDisambiguation:
    def test_random_pr_no_match(self, detector_all):
        pr = {
            "labels": [],
            "user": {"login": "developer"},
            "body": "Normal feature work",
            "head": {"ref": "feature/new-button"},
            "title": "Add button",
        }
        assert detector_all.detect(pr) is None

    def test_dependabot_no_match(self, detector_all):
        pr = {
            "labels": [{"name": "dependencies"}],
            "user": {"login": "dependabot[bot]"},
            "body": "Bumps lodash",
            "head": {"ref": "dependabot/npm/lodash-4.17.21"},
            "title": "chore(deps): bump lodash",
        }
        assert detector_all.detect(pr) is None

    def test_seo_wins_over_codex_when_both_match_weakly(self, detector_all):
        # PR mit SEO-Branch (0.95) und zufaellig "Co-Authored-By: Codex" im Body
        # (0.9 fuer Codex). SEO sollte gewinnen.
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "Co-Authored-By: Codex <noreply@openai.com>",
            "head": {"ref": "seo/test"},
            "title": "x",
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        # SEO 0.95 > Codex 0.9
        assert adapter.agent_name == "seo"

    def test_codex_security_branch_wins_over_title_markers(self, detector_all):
        # Kanonischer Security-Branch (1.0) gewinnt immer
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "fix/security-findings"},
            "title": "[SEO] something confusing",  # 0.9 fuer SEO
        }
        adapter = detector_all.detect(pr)
        assert adapter is not None
        assert adapter.agent_name == "codex"  # 1.0 > 0.9


# ─────────── Partial Adapter Loadouts ───────────

class TestPartialLoadouts:
    def test_jules_only_config(self):
        """Wenn nur Jules aktiv, SEO-PR wird nicht erkannt."""
        d = AgentDetector([JulesAdapter()])
        seo_pr = {
            "labels": [], "user": {"login": "x"},
            "body": "## 🔍 SEO Audit",
            "head": {"ref": "seo/x"}, "title": "[SEO] x",
        }
        assert d.detect(seo_pr) is None

    def test_seo_and_codex_only(self):
        """Ohne Jules werden Jules-PRs nicht erkannt, aber SEO + Codex schon."""
        d = AgentDetector([SeoAdapter(), CodexAdapter()])
        jules_pr = {
            "labels": [{"name": "jules"}],
            "user": {"login": "Commandershadow9"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "jules/x"}, "title": "x",
        }
        assert d.detect(jules_pr) is None

        codex_pr = {
            "labels": [], "user": {"login": "x"},
            "body": "",
            "head": {"ref": "fix/security-findings"},
            "title": "x",
        }
        assert d.detect(codex_pr).agent_name == "codex"
