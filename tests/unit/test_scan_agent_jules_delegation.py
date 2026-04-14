"""Tests fuer SecurityScanAgent → Jules-Queue-Delegation.

Deckt ab:
- _should_delegate_to_jules() Klassifizierungs-Matrix
- _enqueue_jules_fix() Prompt-Bau + Queue-Insert
- Lazy-Property-Accessor (Queue + Enabled-Flag)
- Gracefull Degradation wenn agent_review disabled
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.security_engine.scan_agent import SecurityScanAgent

pytestmark = pytest.mark.asyncio


def _make_agent(*, enabled=True, queue_enqueue=None):
    """Fabrik: SecurityScanAgent mit gestubtem Bot + Queue."""
    agent = SecurityScanAgent.__new__(SecurityScanAgent)
    agent.bot = SimpleNamespace()

    queue = MagicMock()
    queue.enqueue = queue_enqueue or AsyncMock(return_value=42)
    agent.bot.github_integration = SimpleNamespace(
        agent_task_queue=queue,
        _agent_review_enabled=enabled,
    )
    return agent, queue


# ─────────── Lazy-Property-Accessoren ───────────

class TestLazyAccessors:
    async def test_queue_none_when_no_integration(self):
        agent = SecurityScanAgent.__new__(SecurityScanAgent)
        agent.bot = SimpleNamespace()  # kein github_integration
        assert agent.agent_task_queue is None
        assert agent.agent_review_enabled is False

    async def test_queue_from_integration(self):
        agent, queue = _make_agent(enabled=True)
        assert agent.agent_task_queue is queue
        assert agent.agent_review_enabled is True

    async def test_disabled_flag_respected(self):
        agent, _ = _make_agent(enabled=False)
        assert agent.agent_review_enabled is False


# ─────────── _should_delegate_to_jules ───────────

class TestShouldDelegate:
    def _base_finding(self):
        return {
            'category': 'code_security',
            'affected_project': 'zerodox',
            'affected_files': ['web/src/auth.ts'],
            'severity': 'high',
            'title': 'XSS in input',
        }

    async def test_code_security_finding_delegates(self):
        agent, _ = _make_agent(enabled=True)
        assert agent._should_delegate_to_jules(self._base_finding()) is True

    async def test_category_variants_case_insensitive(self):
        agent, _ = _make_agent(enabled=True)
        for cat in ('Code Security', 'code_security', 'CODE_SECURITY', 'XSS', 'auth'):
            f = self._base_finding()
            f['category'] = cat
            assert agent._should_delegate_to_jules(f) is True, f"cat={cat}"

    async def test_docker_finding_does_not_delegate(self):
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['category'] = 'docker'
        assert agent._should_delegate_to_jules(f) is False

    async def test_config_finding_does_not_delegate(self):
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['category'] = 'config'
        assert agent._should_delegate_to_jules(f) is False

    async def test_permissions_finding_does_not_delegate(self):
        """OS-Level Permissions brauchen Server-Zugriff, nicht Code-Fix."""
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['category'] = 'file_permissions'
        assert agent._should_delegate_to_jules(f) is False

    async def test_unknown_project_does_not_delegate(self):
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['affected_project'] = 'randomproject'
        assert agent._should_delegate_to_jules(f) is False

    async def test_empty_affected_files_does_not_delegate(self):
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['affected_files'] = []
        assert agent._should_delegate_to_jules(f) is False

    async def test_missing_affected_files_does_not_delegate(self):
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        del f['affected_files']
        assert agent._should_delegate_to_jules(f) is False

    async def test_disabled_feature_does_not_delegate(self):
        """Safety-Default: wenn agent_review.enabled=false, IMMER GitHub-Issue."""
        agent, _ = _make_agent(enabled=False)
        assert agent._should_delegate_to_jules(self._base_finding()) is False

    async def test_no_github_integration_does_not_delegate(self):
        agent = SecurityScanAgent.__new__(SecurityScanAgent)
        agent.bot = SimpleNamespace()
        assert agent._should_delegate_to_jules(self._base_finding()) is False

    async def test_string_affected_files_normalized(self):
        """Wenn affected_files ein String ist (nicht Liste) → akzeptieren."""
        agent, _ = _make_agent(enabled=True)
        f = self._base_finding()
        f['affected_files'] = 'web/src/auth.ts'
        assert agent._should_delegate_to_jules(f) is True


# ─────────── _enqueue_jules_fix ───────────

class TestEnqueueJulesFix:
    def _finding(self):
        return {
            'category': 'code_security',
            'affected_project': 'zerodox',
            'affected_files': ['web/src/auth.ts', 'web/src/login.tsx'],
            'severity': 'high',
            'title': 'XSS in user input',
            'description': 'User-Input wird als HTML gerendert ohne Sanitizer.',
        }

    async def test_happy_path_returns_task_id(self):
        agent, queue = _make_agent(enabled=True)
        agent._repo_for_finding = lambda f: 'Commandershadow9/ZERODOX'
        task_id = await agent._enqueue_jules_fix(self._finding())
        assert task_id == 42
        queue.enqueue.assert_awaited_once()

    async def test_enqueue_payload_structure(self):
        agent, queue = _make_agent(enabled=True)
        agent._repo_for_finding = lambda f: 'Commandershadow9/ZERODOX'
        await agent._enqueue_jules_fix(self._finding())

        call = queue.enqueue.await_args
        kwargs = call.kwargs
        assert kwargs['source'] == 'scan_agent'
        assert kwargs['priority'] == 1
        assert kwargs['project'] == 'zerodox'
        payload = kwargs['payload']
        assert payload['owner'] == 'Commandershadow9'
        assert payload['repo'] == 'ZERODOX'
        assert payload['branch'] == 'main'
        assert 'XSS in user input' in payload['title']
        assert 'XSS in user input' in payload['prompt']
        assert 'web/src/auth.ts' in payload['prompt']
        assert payload['finding_category'] == 'code_security'
        assert payload['finding_severity'] == 'high'

    async def test_no_queue_returns_none(self):
        agent = SecurityScanAgent.__new__(SecurityScanAgent)
        agent.bot = SimpleNamespace()  # keine integration
        result = await agent._enqueue_jules_fix(self._finding())
        assert result is None

    async def test_invalid_repo_returns_none(self):
        agent, queue = _make_agent(enabled=True)
        agent._repo_for_finding = lambda f: 'invalidrepo'  # kein slash
        result = await agent._enqueue_jules_fix(self._finding())
        assert result is None
        queue.enqueue.assert_not_awaited()

    async def test_enqueue_exception_returns_none(self):
        agent, queue = _make_agent(
            enabled=True,
            queue_enqueue=AsyncMock(side_effect=RuntimeError("db error")),
        )
        agent._repo_for_finding = lambda f: 'Commandershadow9/ZERODOX'
        result = await agent._enqueue_jules_fix(self._finding())
        assert result is None

    async def test_string_affected_files_handled(self):
        agent, queue = _make_agent(enabled=True)
        agent._repo_for_finding = lambda f: 'Commandershadow9/ZERODOX'
        f = self._finding()
        f['affected_files'] = 'single/file.py'
        await agent._enqueue_jules_fix(f)
        payload = queue.enqueue.await_args.kwargs['payload']
        assert 'single/file.py' in payload['prompt']


# ─────────── Constants ───────────

class TestConstants:
    def test_delegatable_categories_present(self):
        cats = SecurityScanAgent._JULES_DELEGATABLE_CATEGORIES
        assert 'code_security' in cats
        assert 'xss' in cats
        assert 'sql_injection' in cats
        assert 'auth' in cats

    def test_infrastructure_categories_NOT_delegatable(self):
        cats = SecurityScanAgent._JULES_DELEGATABLE_CATEGORIES
        for infra in ('docker', 'config', 'permissions', 'file_permissions',
                      'network_exposure', 'backup'):
            assert infra not in cats, f"{infra} should NOT be delegatable"

    def test_known_projects(self):
        p = SecurityScanAgent._JULES_KNOWN_PROJECTS
        for expected in ('zerodox', 'guildscout', 'shadowops-bot',
                         'ai-agent-framework'):
            assert expected in p, f"{expected} should be known"
