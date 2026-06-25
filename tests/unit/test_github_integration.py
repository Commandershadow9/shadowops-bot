"""
Unit Tests for GitHub Integration
"""

import asyncio
import hashlib
import hmac
from unittest.mock import Mock, AsyncMock, MagicMock

import pytest

from src.integrations.github_integration import GitHubIntegration
from src.utils.config import Config


@pytest.fixture
def mock_bot():
    """Mock Discord bot with a channel that can send embeds."""
    channel = Mock()
    channel.send = AsyncMock()
    bot = Mock()
    bot.get_channel = Mock(return_value=channel)
    return bot


@pytest.fixture
def enabled_config():
    """Mock configuration with GitHub enabled."""
    cfg = MagicMock(spec=Config)
    cfg.github = {
        'enabled': True,
        'webhook_secret': 'test_secret',
        'webhook_port': 8080,
        'auto_deploy': True,
        'deploy_branches': ['main']
    }
    cfg.channels = {
        'deployment_log': 12345,
        'code_fixes': 67890
    }
    return cfg


class TestGitHubIntegrationInit:
    """Tests for GitHub Integration initialization."""

    def test_init_with_enabled_config(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)

        assert integration.enabled is True
        assert integration.webhook_secret == 'test_secret'
        assert integration.webhook_port == 8080
        assert integration.auto_deploy_enabled is True
        assert integration.deploy_branches == ['main']
        assert integration.deployment_channel_id == 12345
        assert integration.code_fixes_channel_id == 67890

    def test_init_with_disabled_config(self, mock_bot):
        cfg = MagicMock(spec=Config)
        cfg.github = {'enabled': False}
        cfg.channels = {}

        integration = GitHubIntegration(mock_bot, cfg)

        assert integration.enabled is False
        assert integration.deployment_channel_id == 0


class TestAgentReviewStartup:
    """Tests fuer Agent-Review Initialisierung ohne echte DB/API."""

    class _Config:
        security_analyst_dsn = "postgresql://test/security"
        agent_learning_dsn = "postgresql://test/learning"

        def __init__(self, raw):
            self._config = raw

    class _Queue:
        connected = False

        def __init__(self, dsn):
            self.dsn = dsn

        async def connect(self):
            self.connected = True

    class _OutcomeTracker(_Queue):
        pass

    class _JulesClient:
        def __init__(self, api_key):
            self.api_key = api_key

    class _Poller:
        def __init__(self, queue, jules_api, repos, max_per_run):
            self.queue = queue
            self.jules_api = jules_api
            self.repos = repos
            self.max_per_run = max_per_run

    @staticmethod
    def _patch_agent_review_classes(monkeypatch):
        import src.integrations.github_integration.agent_review.queue as queue_mod
        import src.integrations.github_integration.agent_review.jules_api as api_mod
        import src.integrations.github_integration.agent_review.suggestions_poller as poller_mod
        import src.integrations.github_integration.agent_review.outcome_tracker as outcome_mod

        monkeypatch.setattr(queue_mod, "TaskQueue", TestAgentReviewStartup._Queue)
        monkeypatch.setattr(api_mod, "JulesAPIClient", TestAgentReviewStartup._JulesClient)
        monkeypatch.setattr(poller_mod, "JulesSuggestionsPoller", TestAgentReviewStartup._Poller)
        monkeypatch.setattr(outcome_mod, "OutcomeTracker", TestAgentReviewStartup._OutcomeTracker)

    @pytest.mark.asyncio
    async def test_startup_uses_agent_review_api_key_first(self, mock_bot, monkeypatch):
        self._patch_agent_review_classes(monkeypatch)
        cfg = self._Config({
            "github": {"enabled": True},
            "channels": {},
            "jules_workflow": {"enabled": False, "api_key": "legacy-key"},
            "agent_review": {
                "enabled": True,
                "api_key": "agent-review-key",
                "suggestions_poller": {
                    "enabled": True,
                    "repos": ["Commandershadow9/shadowops-bot"],
                    "max_per_run": 3,
                },
            },
        })

        integration = GitHubIntegration(mock_bot, cfg)
        await integration._agent_review_startup()

        assert integration.jules_api_client.api_key == "agent-review-key"
        assert integration.suggestions_poller is not None
        assert integration.suggestions_poller.repos == ["Commandershadow9/shadowops-bot"]
        assert integration.suggestions_poller.max_per_run == 3

    @pytest.mark.asyncio
    async def test_startup_falls_back_to_legacy_jules_workflow_key(self, mock_bot, monkeypatch):
        self._patch_agent_review_classes(monkeypatch)
        cfg = self._Config({
            "github": {"enabled": True},
            "channels": {},
            "jules_workflow": {"enabled": False, "api_key": "legacy-key"},
            "agent_review": {"enabled": True, "suggestions_poller": {"enabled": False}},
        })

        integration = GitHubIntegration(mock_bot, cfg)
        await integration._agent_review_startup()

        assert integration.jules_api_client.api_key == "legacy-key"
        assert integration.suggestions_poller is None


class TestWebhookVerification:
    """Tests for webhook signature verification."""

    def test_verify_signature_valid(self, mock_bot):
        cfg = MagicMock(spec=Config)
        cfg.github = {'enabled': True, 'webhook_secret': 'secret'}
        cfg.channels = {}
        integration = GitHubIntegration(mock_bot, cfg)

        payload = b'{"key": "value"}'
        signature = 'sha256=' + hmac.new(b'secret', payload, hashlib.sha256).hexdigest()

        assert integration.verify_signature(payload, signature) is True

    def test_verify_signature_invalid(self, mock_bot):
        cfg = MagicMock(spec=Config)
        cfg.github = {'enabled': True, 'webhook_secret': 'secret'}
        cfg.channels = {}
        integration = GitHubIntegration(mock_bot, cfg)

        payload = b'{"key": "value"}'
        invalid_signature = 'sha256=' + hmac.new(b'wrong', payload, hashlib.sha256).hexdigest()

        assert integration.verify_signature(payload, invalid_signature) is False

    @pytest.mark.asyncio
    async def test_project_webhook_prefers_explicit_repo_url(self, mock_bot, enabled_config):
        """Explizite repo_url verhindert Fallback auf kaputte lokale Remotes."""
        enabled_config.github = {
            **enabled_config.github,
            'auto_create_webhooks': True,
            'webhook_public_url': 'https://shadowops.example/webhook',
        }
        enabled_config.projects = {
            'ai-agent-framework': {
                'enabled': True,
                'path': '/home/cmdshadow/agents',
                'repo_url': 'https://github.com/Commandershadow9/ai-agent-framework',
            },
        }
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._get_github_token = Mock(return_value='token')
        integration._get_repo_url = Mock(
            return_value='https://github.com-ai-agent-framework/Commandershadow9/ai-agent-framework'
        )
        integration._ensure_webhook_for_repo = AsyncMock()

        await integration.ensure_project_webhooks()

        integration._get_repo_url.assert_not_called()
        integration._ensure_webhook_for_repo.assert_awaited_once_with(
            project_name='ai-agent-framework',
            repo_url='https://github.com/Commandershadow9/ai-agent-framework',
            github_token='token',
        )

    @pytest.mark.asyncio
    async def test_project_webhook_skips_project_opt_out(self, mock_bot, enabled_config):
        """Monitor-only Projekte duerfen Auto-Webhook explizit abwaehlen."""
        enabled_config.github = {
            **enabled_config.github,
            'auto_create_webhooks': True,
            'webhook_public_url': 'https://shadowops.example/webhook',
        }
        enabled_config.projects = {
            'database-ports': {
                'enabled': True,
                'path': '/home/cmdshadow/agents',
                'auto_create_webhook': False,
            },
        }
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._get_github_token = Mock(return_value='token')
        integration._get_repo_url = Mock()
        integration._ensure_webhook_for_repo = AsyncMock()

        await integration.ensure_project_webhooks()

        integration._get_repo_url.assert_not_called()
        integration._ensure_webhook_for_repo.assert_not_awaited()


class TestPushEventHandling:
    """Tests for push event handling."""

    @pytest.mark.asyncio
    async def test_handle_push_event_to_main(self, mock_bot, enabled_config):
        """Test that direct push to main DOES NOT trigger auto-deploy (Security Hardening)."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()
        # Dedup-Logik mocken — Test darf nicht von persistentem State abhängen
        integration._reserve_commit_processing = Mock(return_value=True)

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo',
                'html_url': 'https://github.com/user/test-repo'
            },
            'ref': 'refs/heads/main',
            'pusher': {'name': 'Tester'},
            'head_commit': {'id': 'abc123def456'},
            'commits': [
                {'id': 'abc123def456', 'message': 'Test commit', 'author': {'name': 'Tester'}}
            ]
        }

        await integration.handle_push_event(payload)

        integration._send_push_notification.assert_called_once()
        # MUST NOT trigger deployment on direct push for security
        integration._trigger_deployment.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_push_event_to_feature_branch(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()
        # Dedup-Logik mocken — Test darf nicht von persistentem State abhängen
        integration._reserve_commit_processing = Mock(return_value=True)

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo',
                'html_url': 'https://github.com/user/test-repo'
            },
            'ref': 'refs/heads/feature-branch',
            'pusher': {'name': 'Dev'},
            'head_commit': {'id': 'abc123'},
            'commits': [
                {'id': 'abc123', 'message': 'Feature commit', 'author': {'name': 'Dev'}}
            ]
        }

        await integration.handle_push_event(payload)

        integration._trigger_deployment.assert_not_called()
        integration._send_push_notification.assert_called_once()


class TestDirectPushAlert:
    """
    Tests fuer den Discord-Alert beim direct-push auf main mit aktivem auto_deploy
    (Auto-Deploy-Block-Notification — verhindert silent inconsistency).
    Hintergrund: PR #135 / Issue #131 — Auto-Deploy auf direct-push ist GEBLOCKT.
    Aber: Niemand wird informiert. Heute (2026-05-24) gab es 3 direct-pushes,
    alle korrekt geblockt, aber kein Alert. Dieser Test fordert das Alert ein.
    """

    @pytest.mark.asyncio
    async def test_direct_push_to_main_triggers_alert(self, mock_bot, enabled_config):
        """Direct-Push auf main + auto_deploy=True → Discord-Alert wird gesendet."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()
        integration._send_direct_push_alert = AsyncMock()
        integration._reserve_commit_processing = Mock(return_value=True)

        payload = {
            'repository': {
                'name': 'shadowops-bot',
                'full_name': 'Commandershadow9/shadowops-bot',
                'html_url': 'https://github.com/Commandershadow9/shadowops-bot'
            },
            'ref': 'refs/heads/main',
            'pusher': {'name': 'CommanderShadow'},
            'head_commit': {'id': 'abc1234567890def'},
            'commits': [
                {
                    'id': 'abc1234567890def',
                    'message': 'fix: typo in docstring\n\nLonger body here',
                    'author': {'name': 'CommanderShadow'}
                }
            ]
        }

        await integration.handle_push_event(payload)

        integration._send_direct_push_alert.assert_called_once()
        call_kwargs = integration._send_direct_push_alert.call_args.kwargs
        assert call_kwargs['repo'] == 'shadowops-bot'
        assert call_kwargs['branch'] == 'main'
        assert call_kwargs['sha'] == 'abc1234567890def'
        assert call_kwargs['pusher'] == 'CommanderShadow'
        # Erste Zeile der Commit-Message kommt im Embed nochmal vor —
        # hier reichen wir die volle message durch, der Builder splittet.
        assert 'fix: typo in docstring' in call_kwargs['commit_message']

    @pytest.mark.asyncio
    async def test_direct_push_to_feature_branch_does_not_trigger_alert(
        self, mock_bot, enabled_config
    ):
        """Push auf non-deploy-branch → KEIN Alert."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()
        integration._send_direct_push_alert = AsyncMock()
        integration._reserve_commit_processing = Mock(return_value=True)

        payload = {
            'repository': {
                'name': 'shadowops-bot',
                'full_name': 'Commandershadow9/shadowops-bot',
                'html_url': 'https://github.com/Commandershadow9/shadowops-bot'
            },
            'ref': 'refs/heads/feat/some-branch',
            'pusher': {'name': 'CommanderShadow'},
            'head_commit': {'id': 'feedface'},
            'commits': [
                {'id': 'feedface', 'message': 'wip', 'author': {'name': 'CommanderShadow'}}
            ]
        }

        await integration.handle_push_event(payload)

        integration._send_direct_push_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_push_alert_is_deduped_per_sha(self, mock_bot, enabled_config):
        """Selber SHA zweimal (force-push) → nur 1 Alert."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()
        integration._reserve_commit_processing = Mock(return_value=True)

        # Wir verifizieren das Dedup-Verhalten via Channel.send Mock (echte Methode laufen lassen).
        channel = mock_bot.get_channel.return_value
        channel.send.reset_mock()

        payload = {
            'repository': {
                'name': 'shadowops-bot',
                'full_name': 'Commandershadow9/shadowops-bot',
                'html_url': 'https://github.com/Commandershadow9/shadowops-bot'
            },
            'ref': 'refs/heads/main',
            'pusher': {'name': 'CommanderShadow'},
            'head_commit': {'id': 'deadbeef12345'},
            'commits': [
                {'id': 'deadbeef12345', 'message': 'fix: thing', 'author': {'name': 'CommanderShadow'}}
            ]
        }

        # Erster Push → Alert
        await integration.handle_push_event(payload)
        first_call_count = channel.send.call_count
        assert first_call_count >= 1, "Erster Direct-Push muss Alert senden"

        # Reset reserve_commit_processing dedup (push handler-level dedup)
        # damit handle_push_event nicht selbst dedupe-skipped — wir wollen den
        # SHA-Dedup im Alert testen, nicht den push-event-Dedup.
        integration._reserve_commit_processing = Mock(return_value=True)

        # Zweiter Push mit gleichem SHA → KEIN zusaetzlicher Alert
        await integration.handle_push_event(payload)
        second_call_count = channel.send.call_count
        assert second_call_count == first_call_count, (
            f"Zweiter Push mit gleichem SHA darf KEINEN weiteren Alert senden "
            f"(first={first_call_count}, second={second_call_count})"
        )

    @pytest.mark.asyncio
    async def test_send_direct_push_alert_uses_deployment_channel(
        self, mock_bot, enabled_config
    ):
        """_send_direct_push_alert resolved deployment_log Channel und sendet Embed."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        channel = mock_bot.get_channel.return_value
        channel.send.reset_mock()

        await integration._send_direct_push_alert(
            repo='shadowops-bot',
            branch='main',
            sha='abc1234567890',
            commit_message='fix: typo',
            pusher='CommanderShadow',
        )

        # Channel-Resolution muss deployment_log nutzen
        mock_bot.get_channel.assert_any_call(enabled_config.channels['deployment_log'])
        # Embed muss gesendet worden sein
        assert channel.send.called
        send_kwargs = channel.send.call_args.kwargs
        assert 'embed' in send_kwargs
        embed = send_kwargs['embed']
        # Titel + Felder pruefen
        assert 'Direct push' in embed.title or 'BLOCKIERT' in embed.title

    @pytest.mark.asyncio
    async def test_send_direct_push_alert_no_channel_logs_warning(
        self, mock_bot, enabled_config
    ):
        """Kein Channel gefunden → Logger-Warnung, kein Crash."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        # Channel-Lookup fehlschlagen lassen
        mock_bot.get_channel = Mock(return_value=None)

        # Darf NICHT crashen
        await integration._send_direct_push_alert(
            repo='shadowops-bot',
            branch='main',
            sha='abc1234567',
            commit_message='fix: x',
            pusher='Tester',
        )
        # Soft-Assertion: keine Exception ist der eigentliche Pass.

    @pytest.mark.asyncio
    async def test_direct_push_alert_cache_eviction(self, mock_bot, enabled_config):
        """Cache evicted alte Eintraege wenn ueber MAX (100) — kein Memory-Leak."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        channel = mock_bot.get_channel.return_value

        # 101 unique SHAs senden — der erste soll evicted sein
        for i in range(101):
            channel.send.reset_mock()
            await integration._send_direct_push_alert(
                repo='shadowops-bot',
                branch='main',
                sha=f'sha{i:08d}',
                commit_message=f'commit {i}',
                pusher='Tester',
            )

        # Cache MUSS <= 100 Eintraege haben (FIFO-Eviction)
        cache = integration._direct_push_alert_cache
        assert len(cache) <= 100, f"Cache exceeded 100 entries: {len(cache)}"


class TestPullRequestHandling:
    """Tests for pull request event handling."""

    @pytest.mark.asyncio
    async def test_handle_pr_opened(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._send_pr_notification = AsyncMock()

        payload = {
            'action': 'opened',
            'repository': {'name': 'test-repo'},
            'pull_request': {
                'number': 42,
                'title': 'Add new feature',
                'user': {'login': 'developer'},
                'html_url': 'https://github.com/user/repo/pull/42',
                'head': {'ref': 'feature-branch'},
                'base': {'ref': 'main'}
            }
        }

        await integration.handle_pull_request_event(payload)

        integration._send_pr_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_pr_merged_to_main(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._send_pr_notification = AsyncMock()
        integration._trigger_deployment = AsyncMock()

        payload = {
            'action': 'closed',
            'repository': {'name': 'test-repo'},
            'pull_request': {
                'number': 42,
                'title': 'Feature PR',
                'user': {'login': 'developer'},
                'html_url': 'https://github.com/user/repo/pull/42',
                'head': {'ref': 'feature'},
                'base': {'ref': 'main'},
                'merged': True,
                'merge_commit_sha': 'abc123def456'
            }
        }

        await integration.handle_pull_request_event(payload)

        integration._trigger_deployment.assert_called_once()
        # Welle 9.10 (2026-05-11): _trigger_deployment muss repo_full_name + full_sha bekommen
        call_kwargs = integration._trigger_deployment.call_args.kwargs
        assert call_kwargs.get('repo_full_name') is None or 'full_name' not in payload['repository']
        assert call_kwargs.get('full_sha') == 'abc123def456'

    @pytest.mark.asyncio
    async def test_handle_pr_merged_passes_full_name_and_sha(self, mock_bot, enabled_config):
        """Welle 9.10: handle_pr_event muss repo_full_name + full_sha durchreichen."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._send_pr_notification = AsyncMock()
        integration._trigger_deployment = AsyncMock()

        payload = {
            'action': 'closed',
            'repository': {
                'name': 'ZERODOX',
                'full_name': 'Commandershadow9/ZERODOX',
            },
            'pull_request': {
                'number': 42,
                'title': 'Feature PR',
                'user': {'login': 'developer'},
                'html_url': 'https://github.com/Commandershadow9/ZERODOX/pull/42',
                'head': {'ref': 'feat/x'},
                'base': {'ref': 'main'},
                'merged': True,
                'merge_commit_sha': 'a' * 40,
            }
        }

        await integration.handle_pull_request_event(payload)

        integration._trigger_deployment.assert_called_once()
        kwargs = integration._trigger_deployment.call_args.kwargs
        assert kwargs['repo_full_name'] == 'Commandershadow9/ZERODOX'
        assert kwargs['full_sha'] == 'a' * 40

    @pytest.mark.asyncio
    async def test_pr_merge_does_not_block_on_deploy(self, mock_bot, enabled_config):
        """#478: Der Merge-Handler darf NICHT synchron auf den (minutenlangen)
        Deploy warten — sonst laeuft GitHubs 10s-Webhook-Timeout ab (504) und der
        Auto-Deploy gilt als fehlgeschlagen. Deploy muss als Background-Task laufen,
        der Handler kehrt sofort zurueck."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._send_pr_notification = AsyncMock()

        deploy_started = asyncio.Event()
        deploy_release = asyncio.Event()

        async def slow_deploy(*args, **kwargs):
            deploy_started.set()
            await deploy_release.wait()  # simuliert einen langlaufenden Deploy

        integration._trigger_deployment = AsyncMock(side_effect=slow_deploy)

        payload = {
            'action': 'closed',
            'repository': {'name': 'mayday-sim', 'full_name': 'Commandershadow9/mayday-sim'},
            'pull_request': {
                'number': 408,
                'title': 'Security PR',
                'user': {'login': 'developer'},
                'html_url': 'https://github.com/Commandershadow9/mayday-sim/pull/408',
                'head': {'ref': 'cmd/security'},
                'base': {'ref': 'main'},
                'merged': True,
                'merge_commit_sha': 'b' * 40,
            },
        }

        # Handler muss schnell zurueckkehren, obwohl der Deploy noch laeuft (kein 504)
        await asyncio.wait_for(integration.handle_pull_request_event(payload), timeout=1.0)
        # Deploy wurde als Hintergrund-Task gestartet und laeuft noch
        await asyncio.wait_for(deploy_started.wait(), timeout=1.0)
        assert not deploy_release.is_set()

        deploy_release.set()  # Cleanup: Background-Task abschliessen lassen
        await asyncio.sleep(0)


class TestWelle910WaitForCI:
    """Welle 9.10 (2026-05-11): _wait_for_ci_completion + _trigger_deployment Wait-Logic."""

    @pytest.fixture
    def cfg_with_projects(self, mock_bot):
        cfg = MagicMock(spec=Config)
        cfg.github = {
            'enabled': True,
            'webhook_secret': 'secret',
            'auto_deploy': True,
            'deploy_branches': ['main'],
        }
        cfg.channels = {'deployment_log': 99999, 'code_fixes': 99998}
        cfg.projects = {
            'zerodox': {
                'enabled': True,
                'ci_workflows': ['Web Quality'],
                'ci_channel_id': 88888,
            },
        }
        return cfg

    @pytest.mark.asyncio
    async def test_wait_returns_no_workflows_when_empty(self, mock_bot, cfg_with_projects):
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='a' * 40,
            workflow_names=[],
            max_wait_min=1,
        )
        assert result == 'no_workflows'

    @pytest.mark.asyncio
    async def test_wait_returns_success_when_all_completed(self, mock_bot, cfg_with_projects):
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration._fetch_workflow_runs_for_sha = AsyncMock(return_value={
            'workflow_runs': [
                {
                    'name': 'Web Quality',
                    'status': 'completed',
                    'conclusion': 'success',
                    'created_at': '2026-05-11T18:00:00Z',
                    'path': '.github/workflows/web-quality.yml',
                },
            ],
        })
        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='a' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=1,
        )
        assert result == 'success'

    @pytest.mark.asyncio
    async def test_wait_returns_failure_on_failed_workflow(self, mock_bot, cfg_with_projects):
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration._fetch_workflow_runs_for_sha = AsyncMock(return_value={
            'workflow_runs': [
                {
                    'name': 'Web Quality',
                    'status': 'completed',
                    'conclusion': 'failure',
                    'created_at': '2026-05-11T18:00:00Z',
                    'path': '.github/workflows/web-quality.yml',
                },
            ],
        })
        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='a' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=1,
        )
        assert result == 'failure'

    @pytest.mark.asyncio
    async def test_wait_uses_latest_run_per_workflow(self, mock_bot, cfg_with_projects):
        """Re-run scenario: ein failed Run + ein successful Re-Run → success."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration._fetch_workflow_runs_for_sha = AsyncMock(return_value={
            'workflow_runs': [
                {
                    'name': 'Web Quality',
                    'status': 'completed',
                    'conclusion': 'failure',
                    'created_at': '2026-05-11T18:00:00Z',
                    'path': '.github/workflows/web-quality.yml',
                },
                {
                    'name': 'Web Quality',
                    'status': 'completed',
                    'conclusion': 'success',
                    'created_at': '2026-05-11T18:10:00Z',  # later
                    'path': '.github/workflows/web-quality.yml',
                },
            ],
        })
        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='a' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=1,
        )
        assert result == 'success'

    @pytest.mark.asyncio
    async def test_wait_returns_timeout_when_pending_too_long(self, mock_bot, cfg_with_projects, monkeypatch):
        """Timeout-Pfad: API liefert immer pending → timeout zurueck."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration._fetch_workflow_runs_for_sha = AsyncMock(return_value={
            'workflow_runs': [
                {
                    'name': 'Web Quality',
                    'status': 'in_progress',
                    'conclusion': None,
                    'created_at': '2026-05-11T18:00:00Z',
                    'path': '.github/workflows/web-quality.yml',
                },
            ],
        })

        # asyncio.sleep stubben damit Test schnell laeuft
        sleep_calls = []

        async def fast_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr('integrations.github_integration.ci_mixin.asyncio.sleep', fast_sleep)

        # time.monotonic stubben damit Schleife nach 2 Iterationen abbricht
        # Sequence: 0 (deadline calc), 0 (loop entry), then advance past deadline
        times = iter([0.0, 0.0, 1000.0, 1000.0])

        def fake_monotonic():
            try:
                return next(times)
            except StopIteration:
                return 1000.0

        monkeypatch.setattr('integrations.github_integration.ci_mixin.time.monotonic', fake_monotonic)

        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='a' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=1,
        )
        assert result == 'timeout'

    @pytest.mark.asyncio
    async def test_wait_returns_no_workflows_after_admin_merge_grace(
        self, mock_bot, cfg_with_projects, monkeypatch,
    ):
        """Welle 9.16 (Issue #243): wenn nach admin_merge_grace_min weiter KEIN
        Workflow für den SHA gesichtet wurde, gilt es als admin-merge ohne CI
        → "no_workflows" (Caller deployt direkt) statt 30min Timeout."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        # API liefert konstant einen Workflow-Run, der NICHT zum Filter passt
        # (anderes Repo / anderer Name). Damit bleibt `relevant` immer leer.
        integration._fetch_workflow_runs_for_sha = AsyncMock(return_value={
            'workflow_runs': [
                {
                    'name': 'Unrelated Workflow',
                    'status': 'completed',
                    'conclusion': 'success',
                    'created_at': '2026-05-15T18:00:00Z',
                    'path': '.github/workflows/unrelated.yml',
                },
            ],
        })

        sleep_calls = []

        async def fast_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr('integrations.github_integration.ci_mixin.asyncio.sleep', fast_sleep)

        # time.monotonic: 0 (started_at), 0 (Loop-Eintritt) ⇒ in Grace,
        # dann 400 (>5*60=300) ⇒ Grace abgelaufen ⇒ no_workflows
        times = iter([0.0, 0.0, 400.0])

        def fake_monotonic():
            try:
                return next(times)
            except StopIteration:
                return 400.0

        monkeypatch.setattr('integrations.github_integration.ci_mixin.time.monotonic', fake_monotonic)

        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='b' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=30,
            admin_merge_grace_min=5,
        )
        assert result == 'no_workflows'

    @pytest.mark.asyncio
    async def test_wait_does_not_short_circuit_when_workflow_was_seen(
        self, mock_bot, cfg_with_projects, monkeypatch,
    ):
        """Negativtest: wenn schon mal ein relevanter Workflow gesehen wurde,
        darf admin_merge_grace NICHT mehr greifen — dann gilt der normale
        Timeout-Pfad."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        # Erst pending (relevant), dann verschwindet er aus dem Response
        # (z.B. weil neuer Re-Run gestartet, aber API noch nicht aktuell).
        call_count = {'n': 0}

        async def fetch(*_args, **_kwargs):
            call_count['n'] += 1
            if call_count['n'] == 1:
                return {
                    'workflow_runs': [
                        {
                            'name': 'Web Quality',
                            'status': 'in_progress',
                            'conclusion': None,
                            'created_at': '2026-05-15T18:00:00Z',
                            'path': '.github/workflows/web-quality.yml',
                        },
                    ],
                }
            return {'workflow_runs': []}

        integration._fetch_workflow_runs_for_sha = fetch

        async def fast_sleep(_seconds):
            return None

        monkeypatch.setattr('integrations.github_integration.ci_mixin.asyncio.sleep', fast_sleep)

        # Erste Iteration: in_progress → saw_any_relevant=True, weiter pollen
        # Zweite Iteration: relevant leer, ABER saw_any_relevant=True → kein short-circuit
        # Dritte Iteration: deadline überschritten → timeout
        times = iter([0.0, 0.0, 100.0, 2000.0])

        def fake_monotonic():
            try:
                return next(times)
            except StopIteration:
                return 2000.0

        monkeypatch.setattr('integrations.github_integration.ci_mixin.time.monotonic', fake_monotonic)

        result = await integration._wait_for_ci_completion(
            repo_full_name='Commandershadow9/ZERODOX',
            merged_sha='c' * 40,
            workflow_names=['Web Quality'],
            max_wait_min=30,
            admin_merge_grace_min=1,
        )
        assert result == 'timeout'

    @pytest.mark.asyncio
    async def test_trigger_deployment_blocks_on_failure(self, mock_bot, cfg_with_projects):
        """deploy.sh darf NICHT laufen wenn CI failed."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration.deployment_manager = MagicMock()
        integration.deployment_manager.deploy_project = AsyncMock(return_value={'success': True})
        integration._wait_for_ci_completion = AsyncMock(return_value='failure')
        integration._send_ci_wait_alert = AsyncMock()

        await integration._trigger_deployment(
            repo_name='zerodox',
            branch='main',
            commit_sha='abc1234',
            repo_full_name='Commandershadow9/ZERODOX',
            full_sha='a' * 40,
        )

        integration.deployment_manager.deploy_project.assert_not_called()
        integration._send_ci_wait_alert.assert_called_once()
        kwargs = integration._send_ci_wait_alert.call_args.kwargs
        assert kwargs['outcome'] == 'failure'

    @pytest.mark.asyncio
    async def test_trigger_deployment_blocks_on_timeout(self, mock_bot, cfg_with_projects):
        """deploy.sh darf NICHT laufen wenn CI Timeout."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration.deployment_manager = MagicMock()
        integration.deployment_manager.deploy_project = AsyncMock(return_value={'success': True})
        integration._wait_for_ci_completion = AsyncMock(return_value='timeout')
        integration._send_ci_wait_alert = AsyncMock()

        await integration._trigger_deployment(
            repo_name='zerodox',
            branch='main',
            commit_sha='abc1234',
            repo_full_name='Commandershadow9/ZERODOX',
            full_sha='a' * 40,
        )

        integration.deployment_manager.deploy_project.assert_not_called()
        integration._send_ci_wait_alert.assert_called_once()
        kwargs = integration._send_ci_wait_alert.call_args.kwargs
        assert kwargs['outcome'] == 'timeout'

    @pytest.mark.asyncio
    async def test_trigger_deployment_proceeds_on_success(self, mock_bot, cfg_with_projects):
        """deploy.sh MUSS laufen wenn CI success."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration.deployment_manager = MagicMock()
        integration.deployment_manager.deploy_project = AsyncMock(return_value={'success': True})
        integration._wait_for_ci_completion = AsyncMock(return_value='success')
        integration._send_ci_wait_alert = AsyncMock()

        await integration._trigger_deployment(
            repo_name='zerodox',
            branch='main',
            commit_sha='abc1234',
            repo_full_name='Commandershadow9/ZERODOX',
            full_sha='a' * 40,
        )

        integration.deployment_manager.deploy_project.assert_called_once_with('zerodox', 'main')
        integration._send_ci_wait_alert.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_deployment_skips_wait_when_no_full_args(self, mock_bot, cfg_with_projects):
        """Backward-Compat: alte Caller ohne repo_full_name/full_sha → kein Wait."""
        integration = GitHubIntegration(mock_bot, cfg_with_projects)
        integration.deployment_manager = MagicMock()
        integration.deployment_manager.deploy_project = AsyncMock(return_value={'success': True})
        integration._wait_for_ci_completion = AsyncMock()

        await integration._trigger_deployment(
            repo_name='zerodox',
            branch='main',
            commit_sha='abc1234',
        )

        integration._wait_for_ci_completion.assert_not_called()
        integration.deployment_manager.deploy_project.assert_called_once()


class TestReleaseHandling:
    """Tests for release event handling."""

    @pytest.mark.asyncio
    async def test_handle_release_published(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._send_release_notification = AsyncMock()

        payload = {
            'action': 'published',
            'repository': {'name': 'test-repo'},
            'release': {
                'tag_name': 'v1.2.3',
                'name': 'Version 1.2.3',
                'author': {'login': 'maintainer'},
                'html_url': 'https://github.com/user/repo/releases/tag/v1.2.3',
                'prerelease': False
            }
        }

        await integration.handle_release_event(payload)

        integration._send_release_notification.assert_called_once()


class TestNotificationsProjectLookup:
    """Regressionstests fuer dash/underscore-Projektnamen in Notifications."""

    def test_project_config_lookup_matches_dash_to_underscore(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {
            'mayday_sim': {'path': '/srv/leitstelle/app', 'patch_notes': {'engine': 'v6'}},
        }

        project_config, project_key = integration._get_project_config_for_repo('mayday-sim')

        assert project_key == 'mayday_sim'
        assert project_config['path'] == '/srv/leitstelle/app'

    def test_last_version_from_git_uses_normalized_project_path(
        self, mock_bot, enabled_config, monkeypatch
    ):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {
            'mayday_sim': {'path': '/srv/leitstelle/app'},
        }

        calls = []

        def fake_run(*args, **kwargs):
            calls.append(kwargs)
            result = MagicMock()
            result.returncode = 0
            result.stdout = 'v1.2.3\n'
            return result

        monkeypatch.setattr('subprocess.run', fake_run)

        assert integration._get_last_version_from_git('mayday-sim') == '1.2.3'
        assert calls[0]['cwd'] == '/srv/leitstelle/app'

    def test_commit_tag_version_uses_normalized_project_path(
        self, mock_bot, enabled_config, monkeypatch
    ):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {
            'mayday_sim': {'path': '/srv/leitstelle/app'},
        }

        calls = []

        def fake_run(*args, **kwargs):
            calls.append(kwargs)
            result = MagicMock()
            result.returncode = 0
            result.stdout = 'v2.0.0 abc123 abc123\n'
            return result

        monkeypatch.setattr('subprocess.run', fake_run)

        version = integration._get_version_from_commit_tags(
            commits=[{'id': 'abc123ffff', 'message': 'release'}],
            repo_name='mayday-sim',
        )

        assert version == '2.0.0'
        assert calls[0]['cwd'] == '/srv/leitstelle/app'


class TestV6BatcherGate:
    """Regression: v6 darf bei Webhook NIE direkt releasen — nur via Cron/manuell/Notbremse.

    Hintergrund: Vorfall 2026-04-15. Vor dem Fix erzeugte jeder Push einen
    eigenen Mini-Release (z.B. zerodox 1.3.2 → 1.3.3 → 1.3.4 …) statt zu
    sammeln. Der Batcher MUSS zwischen Webhook und Pipeline.run() liegen.
    """

    @pytest.fixture
    def v6_project_config(self):
        return {
            'patch_notes': {'engine': 'v6', 'language': 'de'},
            'color': 0x00FF00,
        }

    @pytest.mark.asyncio
    async def test_webhook_only_batches_no_pipeline_run(self, mock_bot, enabled_config, v6_project_config, monkeypatch):
        """Webhook-Push (skip_batcher=False) → batcher.add_commits, KEIN pipeline.run."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {'demo': v6_project_config}

        batcher = MagicMock()
        batcher.add_commits = MagicMock(return_value={'ready': False, 'total_pending': 7})
        batcher.release_batch = MagicMock(return_value=None)
        integration.patch_notes_batcher = batcher

        pipeline_run = AsyncMock()
        monkeypatch.setattr('patch_notes.pipeline.PatchNotePipeline.run', pipeline_run)

        await integration._send_push_notification(
            repo_name='demo', repo_url='', branch='main', pusher='alice',
            commits=[{'id': 'abc', 'message': 'feat: x'}],
            skip_batcher=False,
        )

        batcher.add_commits.assert_called_once()
        pipeline_run.assert_not_awaited()
        batcher.release_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_never_releases_even_at_high_volume(self, mock_bot, enabled_config, v6_project_config, monkeypatch):
        """Auch bei 50+ Commits darf der Webhook NICHT direkt releasen.
        Cron / /release-notes ist die einzige Release-Quelle."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {'demo': v6_project_config}

        batcher = MagicMock()
        batcher.add_commits = MagicMock(return_value={'ready': False, 'total_pending': 73})
        batcher.release_batch = MagicMock(return_value=None)
        integration.patch_notes_batcher = batcher

        pipeline_run = AsyncMock()
        monkeypatch.setattr('patch_notes.pipeline.PatchNotePipeline.run', pipeline_run)

        await integration._send_push_notification(
            repo_name='demo', repo_url='', branch='main', pusher='alice',
            commits=[{'id': f'c{i}', 'message': f'feat: {i}'} for i in range(15)],
            skip_batcher=False,
        )

        batcher.add_commits.assert_called_once()
        pipeline_run.assert_not_awaited()
        batcher.release_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_batcher_true_runs_pipeline_directly(self, mock_bot, enabled_config, v6_project_config, monkeypatch):
        """Cron/manueller Pfad (skip_batcher=True) umgeht den Batcher und ruft pipeline.run."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {'demo': v6_project_config}

        batcher = MagicMock()
        integration.patch_notes_batcher = batcher

        pipeline_run = AsyncMock()
        monkeypatch.setattr('patch_notes.pipeline.PatchNotePipeline.run', pipeline_run)

        await integration._send_push_notification(
            repo_name='demo', repo_url='', branch='main', pusher='cron',
            commits=[{'id': 'a', 'message': 'feat: x'}, {'id': 'b', 'message': 'fix: y'}],
            skip_batcher=True,
        )

        batcher.add_commits.assert_not_called()
        pipeline_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_batcher_fails_closed_no_pipeline_run(self, mock_bot, enabled_config, v6_project_config, monkeypatch):
        """Wenn der Batcher fehlt, NICHT releasen (fail-closed gegen Spam)."""
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration.config.projects = {'demo': v6_project_config}
        integration.patch_notes_batcher = None
        # Auch am Bot nicht vorhanden
        mock_bot.patch_notes_batcher = None

        pipeline_run = AsyncMock()
        monkeypatch.setattr('patch_notes.pipeline.PatchNotePipeline.run', pipeline_run)

        await integration._send_push_notification(
            repo_name='demo', repo_url='', branch='main', pusher='alice',
            commits=[{'id': 'a', 'message': 'feat: x'}],
            skip_batcher=False,
        )

        pipeline_run.assert_not_awaited()
