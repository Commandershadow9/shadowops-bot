"""
Unit Tests for GitHub Integration
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from aiohttp import web

from src.integrations.github_integration import GitHubIntegration


class TestGitHubIntegrationInit:
    """Tests for GitHub Integration initialization"""

    def test_init_with_enabled_config(self, mock_config):
        """Test initialization with GitHub enabled"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'webhook_secret': 'test_secret',
                'webhook_port': 8080,
                'auto_deploy': True,
                'deploy_branches': ['main', 'production']
            },
            'channels': {
                'deployment_log': 12345
            }
        }

        mock_bot = Mock()
        integration = GitHubIntegration(mock_bot, mock_config_dict)

        assert integration.enabled is True
        assert integration.webhook_secret == 'test_secret'
        assert integration.webhook_port == 8080
        assert integration.auto_deploy_enabled is True
        assert integration.deploy_branches == ['main', 'production']

    def test_init_with_disabled_config(self, mock_config):
        """Test initialization with GitHub disabled"""
        mock_config_dict = {
            'github': {'enabled': False},
            'channels': {}
        }

        mock_bot = Mock()
        integration = GitHubIntegration(mock_bot, mock_config_dict)

        assert integration.enabled is False


class TestWebhookVerification:
    """Tests for webhook signature verification"""

    def test_verify_signature_valid(self, mock_config):
        """Test valid signature verification"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'webhook_secret': 'test_secret'
            },
            'channels': {}
        }

        mock_bot = Mock()
        integration = GitHubIntegration(mock_bot, mock_config_dict)

        body = b'{"test": "payload"}'

        # Calculate correct signature
        import hmac
        import hashlib
        mac = hmac.new(
            b'test_secret',
            msg=body,
            digestmod=hashlib.sha256
        )
        correct_signature = f"sha256={mac.hexdigest()}"

        assert integration._verify_signature(body, correct_signature) is True

    def test_verify_signature_invalid(self, mock_config):
        """Test invalid signature verification"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'webhook_secret': 'test_secret'
            },
            'channels': {}
        }

        mock_bot = Mock()
        integration = GitHubIntegration(mock_bot, mock_config_dict)

        body = b'{"test": "payload"}'
        invalid_signature = "sha256=invalid_signature"

        assert integration._verify_signature(body, invalid_signature) is False


class TestPushEventHandling:
    """Tests for push event handling"""

    @pytest.mark.asyncio
    async def test_handle_push_event_to_main(self, mock_config):
        """Test handling push event to main branch"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'auto_deploy': True,
                'deploy_branches': ['main']
            },
            'channels': {'deployment_log': 12345}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        integration = GitHubIntegration(mock_bot, mock_config_dict)

        # Mock deployment trigger
        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo'
            },
            'ref': 'refs/heads/main',
            'commits': [
                {
                    'id': 'abc123def456',
                    'message': 'Test commit',
                    'author': {'name': 'Test User'}
                }
            ]
        }

        await integration.handle_push_event(payload)

        # Should trigger deployment for main branch
        integration._trigger_deployment.assert_called_once()
        integration._send_push_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_push_event_to_feature_branch(self, mock_config):
        """Test handling push event to non-deployment branch"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'auto_deploy': True,
                'deploy_branches': ['main']
            },
            'channels': {'deployment_log': 12345}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        integration = GitHubIntegration(mock_bot, mock_config_dict)

        integration._trigger_deployment = AsyncMock()
        integration._send_push_notification = AsyncMock()

        payload = {
            'repository': {
                'name': 'test-repo',
                'full_name': 'user/test-repo'
            },
            'ref': 'refs/heads/feature-branch',
            'commits': [
                {
                    'id': 'abc123',
                    'message': 'Feature commit',
                    'author': {'name': 'Developer'}
                }
            ]
        }

        await integration.handle_push_event(payload)

        # Should NOT trigger deployment for feature branch
        integration._trigger_deployment.assert_not_called()
        # But should still send notification
        integration._send_push_notification.assert_called_once()


class TestPullRequestHandling:
    """Tests for pull request event handling"""

    @pytest.mark.asyncio
    async def test_handle_pr_opened(self, mock_config):
        """Test handling PR opened event"""
        mock_config_dict = {
            'github': {'enabled': True},
            'channels': {'deployment_log': 12345}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        integration = GitHubIntegration(mock_bot, mock_config_dict)
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

        await integration.handle_pr_event(payload)

        integration._send_pr_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_pr_merged_to_main(self, mock_config):
        """Test handling PR merged to deployment branch"""
        mock_config_dict = {
            'github': {
                'enabled': True,
                'auto_deploy': True,
                'deploy_branches': ['main']
            },
            'channels': {'deployment_log': 12345}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        integration = GitHubIntegration(mock_bot, mock_config_dict)
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

        await integration.handle_pr_event(payload)

        # Should trigger deployment when merged to main
        integration._trigger_deployment.assert_called_once()


class TestReleaseHandling:
    """Tests for release event handling"""

    @pytest.mark.asyncio
    async def test_handle_release_published(self, mock_config):
        """Test handling release published event"""
        mock_config_dict = {
            'github': {'enabled': True},
            'channels': {'deployment_log': 12345}
        }

        mock_bot = Mock()
        mock_bot.get_channel = Mock(return_value=None)

        integration = GitHubIntegration(mock_bot, mock_config_dict)
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


@pytest.fixture
def mock_config():
    """Mock configuration"""
    return {
        'github': {
            'enabled': True,
            'webhook_secret': 'test_secret',
            'webhook_port': 8080,
            'auto_deploy': False,
            'deploy_branches': ['main']
        },
        'channels': {
            'deployment_log': 12345
        }
    }
