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
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo'
            },
            'ref': 'refs/heads/main',
            'commits': [
                {'id': 'abc123def456', 'message': 'Test commit', 'author': {'name': 'Tester'}}
            ]
        }

        await integration.handle_push_event(payload)

        integration._send_push_notification.assert_called_once()
        integration._trigger_deployment.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_push_event_to_feature_branch(self, mock_bot, enabled_config):
        integration = GitHubIntegration(mock_bot, enabled_config)
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo'
            },
            'ref': 'refs/heads/feature-branch',
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
