"""Tests fuer SeoAdapter."""
import pytest

from src.integrations.github_integration.agent_review.adapters.seo import (
    SeoAdapter,
    SAFE_CONTENT_EXTENSIONS,
    DANGEROUS_PATHS,
    MAX_FILES_FOR_AUTO_MERGE,
)
from src.integrations.github_integration.agent_review.adapters.base import (
    MergeDecision,
)


@pytest.fixture
def adapter():
    return SeoAdapter()


# ──────── detect() ────────

class TestDetect:
    def test_audit_body_full_confidence(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "Commandershadow9"},
            "body": "## 🔍 SEO Audit — Automatische Fixes\n\nWebsite: guildscout",
            "head": {"ref": "main"},
            "title": "irgendwas",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 1.0
        assert d.metadata["src"] == "audit_body"

    def test_branch_seo_prefix(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "seo/zerodox/2026-04-14"},
            "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.95
        assert d.metadata["src"] == "branch"

    def test_title_bracket_seo(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "main"},
            "title": "[SEO] Blog-Artikel: c/o adresse",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.9

    def test_title_seo_colon(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "",
            "head": {"ref": "main"},
            "title": "SEO: Automatische Optimierungen fuer zerodox",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.85

    def test_seo_agent_in_body(self, adapter):
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "Co-Authored-By: SEO Agent <noreply@seo-agent.local>",
            "head": {"ref": "main"},
            "title": "x",
        }
        d = adapter.detect(pr)
        assert d.matched is True
        assert d.confidence == 0.85

    def test_no_match_for_jules_pr(self, adapter):
        pr = {
            "labels": [{"name": "jules"}],
            "user": {"login": "google-labs-jules[bot]"},
            "body": "PR created automatically by Jules",
            "head": {"ref": "fix/security"},
            "title": "Fix XSS",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_no_match_for_dependabot(self, adapter):
        pr = {
            "labels": [{"name": "dependencies"}],
            "user": {"login": "dependabot[bot]"},
            "body": "Bumps the npm group",
            "head": {"ref": "dependabot/npm/lodash-4.17.21"},
            "title": "chore(deps): bump lodash",
        }
        d = adapter.detect(pr)
        assert d.matched is False

    def test_audit_body_priority_over_branch(self, adapter):
        # Wenn beide passen, sollte audit_body (1.0) gewinnen
        pr = {
            "labels": [],
            "user": {"login": "x"},
            "body": "## 🔍 SEO Audit\n...",
            "head": {"ref": "seo/x"},
            "title": "x",
        }
        d = adapter.detect(pr)
        assert d.confidence == 1.0


# ──────── model_preference() ────────

class TestModelPreference:
    def test_default_sonnet(self, adapter):
        pr = {"title": "[SEO] kleine Aenderung"}
        primary, fallback = adapter.model_preference(pr, diff_len=500)
        assert primary == "standard"
        assert fallback == "thinking"

    def test_large_diff_opus(self, adapter):
        pr = {"title": "SEO: neue Seitenstruktur"}
        primary, _ = adapter.model_preference(pr, diff_len=8000)
        assert primary == "thinking"


# ──────── merge_policy() ────────

class TestMergePolicy:
    def _approved(self, in_scope=True):
        return {"verdict": "approved", "scope_check": {"in_scope": in_scope}}

    def test_blocked_for_frozen_project(self, adapter):
        pr = {"files_changed_paths": ["x.md"], "additions": 10}
        assert adapter.merge_policy(self._approved(), pr, "sicherheitsdienst") == MergeDecision.MANUAL

    def test_manual_when_not_approved(self, adapter):
        review = {"verdict": "revision_requested", "scope_check": {"in_scope": True}}
        pr = {"files_changed_paths": ["web/src/content/x.md"], "additions": 50}
        assert adapter.merge_policy(review, pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_out_of_scope(self, adapter):
        pr = {"files_changed_paths": ["web/src/content/x.md"], "additions": 50}
        assert adapter.merge_policy(self._approved(in_scope=False), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_too_many_files(self, adapter):
        paths = [f"web/src/content/blog-{i}.md" for i in range(60)]
        pr = {"files_changed_paths": paths, "additions": 1000}
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_touches_package_json(self, adapter):
        pr = {
            "files_changed_paths": [
                "web/src/content/x.md",
                "web/package.json",
            ],
            "additions": 50,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_touches_layout(self, adapter):
        pr = {
            "files_changed_paths": ["web/src/app/layout.tsx"],
            "additions": 10,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_touches_prisma(self, adapter):
        pr = {
            "files_changed_paths": ["web/prisma/schema.prisma"],
            "additions": 5,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_auto_for_pure_content(self, adapter):
        pr = {
            "files_changed_paths": [
                "web/src/content/blog-a.md",
                "web/src/content/blog-b.mdx",
            ],
            "additions": 200,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.AUTO

    def test_auto_for_metadata_only(self, adapter):
        pr = {
            "files_changed_paths": [
                "web/src/lib/sitemap.ts",
                "public/robots.txt",
            ],
            "additions": 30,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.AUTO

    def test_auto_for_blog_data(self, adapter):
        pr = {
            "files_changed_paths": ["web/src/lib/blog-data.ts"],
            "additions": 100,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.AUTO

    def test_manual_for_mixed_paths(self, adapter):
        pr = {
            "files_changed_paths": [
                "web/src/content/blog.md",
                "web/src/components/Hero.tsx",  # not safe
            ],
            "additions": 100,
        }
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL

    def test_manual_when_no_paths(self, adapter):
        pr = {"files_changed_paths": [], "additions": 10}
        assert adapter.merge_policy(self._approved(), pr, "ZERODOX") == MergeDecision.MANUAL


# ──────── discord_channel + iteration_mention ────────

class TestDiscordAndMention:
    def test_discord_channel_constant(self, adapter):
        assert adapter.discord_channel("approved") == "seo-fixes"
        assert adapter.discord_channel("revision_requested") == "seo-fixes"

    def test_no_iteration_mention(self, adapter):
        # SEO-Agent ist lokaler Code, kein Bot — kein @mention
        assert adapter.iteration_mention() is None


# ──────── Constants Export ────────

class TestConstants:
    def test_safe_extensions(self):
        assert ".md" in SAFE_CONTENT_EXTENSIONS
        assert ".mdx" in SAFE_CONTENT_EXTENSIONS

    def test_dangerous_paths_complete(self):
        assert any("package.json" in p for p in DANGEROUS_PATHS)
        assert any("layout.tsx" in p for p in DANGEROUS_PATHS)
        assert any("prisma" in p for p in DANGEROUS_PATHS)

    def test_max_files_threshold(self):
        assert MAX_FILES_FOR_AUTO_MERGE == 50


# ──────── agent_name ────────

def test_agent_name(adapter):
    assert adapter.agent_name == "seo"


# ──────── _is_safe_path utility ────────

class TestIsSafePath:
    def test_md_file_safe(self, adapter):
        assert adapter._is_safe_path("web/src/content/blog.md") is True

    def test_mdx_file_safe(self, adapter):
        assert adapter._is_safe_path("docs/guide.mdx") is True

    def test_sitemap_safe(self, adapter):
        assert adapter._is_safe_path("web/src/lib/sitemap.ts") is True

    def test_robots_safe(self, adapter):
        assert adapter._is_safe_path("public/robots.txt") is True

    def test_blog_data_safe(self, adapter):
        assert adapter._is_safe_path("web/src/lib/blog-data.ts") is True

    def test_random_ts_unsafe(self, adapter):
        assert adapter._is_safe_path("web/src/components/Hero.tsx") is False

    def test_random_py_unsafe(self, adapter):
        assert adapter._is_safe_path("src/foo.py") is False
