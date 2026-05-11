"""
Unit Tests for GitHub Integration
"""

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
