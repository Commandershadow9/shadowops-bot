"""Tests fuer Issue #249 — SecurityScanAgent Routing:

- Host-OS-Findings (Kernel, ImageMagick, Ownership-Drift, etc.) duerfen
  KEIN GitHub-Issue im shadowops-bot Repo erzeugen.
- Bot-Code-Findings (npm_audit, code_security, etc.) gehen weiter normal durch.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.security_engine.scan_agent import (
    HOST_OS_CATEGORIES,
    SecurityScanAgent,
)

pytestmark = pytest.mark.asyncio


def _make_minimal_agent():
    """Minimal-Agent fuer _create_github_issue-Routing-Tests."""
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.bot = SimpleNamespace()
    # Fingerprint-Check umgehen — Test fokussiert nur auf Host-OS-Routing
    agent._find_similar_open_finding_by_fingerprint = AsyncMock(
        return_value=(None, None)
    )
    return agent


class TestHostOsCategoriesShortCircuit:
    """Issue #249: Host-OS-Kategorien fuehren zu return None ohne subprocess."""

    async def test_kernel_update_returns_none(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'kernel_update',
            'affected_project': 'infrastructure',
            'title': 'Kernel security update verfuegbar',
            'description': 'Neue CVEs im linux-image-cloud-amd64 verfuegbar.',
            'severity': 'medium',
        }
        result = await agent._create_github_issue(finding)
        assert result is None
        # Fingerprint-Dedupe darf NICHT gerufen worden sein (early-return davor)
        agent._find_similar_open_finding_by_fingerprint.assert_not_called()

    async def test_imagemagick_returns_none(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'imagemagick',
            'affected_project': 'infrastructure',
            'title': 'ImageMagick deb12u9',
            'description': 'Security-Backlog auf Debian 12 schliessen.',
            'severity': 'medium',
        }
        assert await agent._create_github_issue(finding) is None

    async def test_ownership_drift_returns_none(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'ownership_drift',
            'affected_project': 'infrastructure',
            'title': 'Ownership-Drift auf Root-Pfaden',
            'description': 'setuid-Binaries gehoeren nobody statt root.',
            'severity': 'critical',
        }
        assert await agent._create_github_issue(finding) is None

    async def test_container_image_cve_returns_none(self):
        """blogger-mcp / leitstelle-osrm CVEs gehoeren in externes Repo, nicht hier."""
        agent = _make_minimal_agent()
        finding = {
            'category': 'container_image_cve',
            'affected_project': 'infrastructure',
            'title': 'blogger-mcp HIGH-CVE-Last gestiegen',
            'description': '+43 HIGH-CVEs seit 2026-04-24 im public Container.',
            'severity': 'high',
        }
        assert await agent._create_github_issue(finding) is None

    async def test_category_case_insensitive(self):
        """KERNEL_UPDATE oder Kernel_Update werden gleich behandelt."""
        agent = _make_minimal_agent()
        finding = {
            'category': 'KERNEL_UPDATE',
            'affected_project': 'infrastructure',
            'title': 'Kernel-Update',
            'description': 'Sicherheits-Updates ausstehend auf Host.',
            'severity': 'medium',
        }
        assert await agent._create_github_issue(finding) is None


class TestNonHostOsContinuesNormally:
    """Bot-Code-Findings (npm_audit, code_security) fallen NICHT auf den
    Early-Return — sie laufen weiter in den Fingerprint-Check und ggf.
    gh issue create."""

    async def test_npm_audit_continues_past_routing(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'npm_audit',
            'affected_project': 'zerodox',
            'title': 'npm-Package mit CVE',
            'description': 'Dependency XYZ hat CVE-2026-1234.',
            'severity': 'high',
        }
        # _find_similar_open_finding_by_fingerprint MUSS gerufen werden
        # (= wir sind ueber den Host-OS-Check hinaus). Danach greift der
        # PYTEST_CURRENT_TEST-Guard → return None ohne echten gh-Subprocess
        # (Test-Isolation, Vorfall #1069/#1070). Der Test prueft beides:
        # Fingerprint-Check wurde gerufen UND kein echtes Issue erstellt.
        result = await agent._create_github_issue(finding)
        agent._find_similar_open_finding_by_fingerprint.assert_called_once()
        assert result is None  # Guard verhindert echte Issue-Erstellung in Tests

    async def test_code_security_continues_past_routing(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'code_security',
            'affected_project': 'zerodox',
            'title': 'XSS in user input',
            'description': 'User-Input wird ohne Sanitizer als HTML gerendert.',
            'severity': 'high',
            'affected_files': ['web/src/auth.ts'],
        }
        result = await agent._create_github_issue(finding)
        agent._find_similar_open_finding_by_fingerprint.assert_called_once()
        assert result is None  # Guard verhindert echte Issue-Erstellung in Tests


class TestPytestIssueIsolation:
    """Vorfall 2026-05-29: Test-Fixtures (z.B. 'Dependency XYZ hat
    CVE-2026-1234.') erzeugten echte Production-Issues #1069/#1070 — weil
    _create_github_issue den gh-Subprocess ungemockt aufrief und auf einer
    Maschine mit authentifiziertem gh CLI lief. Der PYTEST_CURRENT_TEST-Guard
    verhindert in Tests JEDEN echten gh-Subprocess."""

    async def test_guard_skips_real_gh_subprocess_in_pytest(self):
        agent = _make_minimal_agent()
        finding = {
            'category': 'code_security',
            'affected_project': 'zerodox',
            'title': 'SQL Injection in query builder',
            'description': 'Nutzereingabe wird unescaped in SQL konkateniert.',
            'severity': 'high',
            'affected_files': ['web/src/db.ts'],
        }
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            result = await agent._create_github_issue(finding)
        assert result is None
        # KEIN gh-Subprocess (weder issue list noch issue create) in pytest.
        mock_exec.assert_not_called()


class TestHostOsCategoriesConstant:
    """Konstante selbst testen."""

    def test_contains_kernel_categories(self):
        assert 'kernel_update' in HOST_OS_CATEGORIES
        assert 'kernel_patch' in HOST_OS_CATEGORIES

    def test_contains_imagemagick(self):
        assert 'imagemagick' in HOST_OS_CATEGORIES

    def test_contains_ownership_drift(self):
        assert 'ownership_drift' in HOST_OS_CATEGORIES
        assert 'setuid_drift' in HOST_OS_CATEGORIES

    def test_contains_external_container_cve(self):
        assert 'container_image_cve' in HOST_OS_CATEGORIES

    def test_all_entries_lowercase(self):
        """Pflicht-Eigenschaft fuer den case-insensitiven Lookup."""
        for cat in HOST_OS_CATEGORIES:
            assert cat == cat.lower(), f"Eintrag {cat!r} ist nicht lower-case"
