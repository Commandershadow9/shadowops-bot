"""
GitHub Integration for ShadowOps Bot
Handles webhook events, auto-deployment, and Discord notifications
"""

import asyncio
import logging
from typing import Dict, Callable

from utils.state_manager import get_state_manager

from .webhook_mixin import WebhookMixin
from .polling_mixin import PollingMixin
from .event_handlers_mixin import EventHandlersMixin
from .ci_mixin import CIMixin
from .state_mixin import StateMixin
from .git_ops_mixin import GitOpsMixin
from .notifications_mixin import NotificationsMixin
from .ai_patch_notes_mixin import AIPatchNotesMixin

logger = logging.getLogger('shadowops')


class GitHubIntegration(WebhookMixin, PollingMixin, EventHandlersMixin, CIMixin,
                         StateMixin, GitOpsMixin, NotificationsMixin, AIPatchNotesMixin):
    """
    GitHub webhook integration for deployment automation

    Features:
    - Webhook listener for push/PR/release events
    - Auto-deploy on main branch pushes
    - Discord notifications for all events
    - HMAC signature verification for security
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize GitHub integration

        Args:
            bot: Discord bot instance
            config: Configuration dictionary with github settings
        """
        self.bot = bot
        self.config = config
        self.logger = logger
        self.ai_service = None  # Will be set by bot after AI Service is initialized
        self.state_manager = get_state_manager()

        def _get_section(name: str, default=None):
            """Safely fetch config sections from dicts or Config objects."""
            if default is None:
                default = {}
            if isinstance(config, dict):
                return config.get(name, default)
            section = getattr(config, name, None)
            if section is not None:
                return section
            base_config = getattr(config, '_config', None)
            if isinstance(base_config, dict):
                return base_config.get(name, default)
            return default

        # GitHub webhook settings
        github_config = _get_section('github', {})
        self.webhook_secret = github_config.get('webhook_secret', '')
        self.webhook_port = github_config.get('webhook_port', 8080)
        self.enabled = github_config.get('enabled', False)
        self.auto_deploy_enabled = github_config.get('auto_deploy', False)
        self.deploy_branches = github_config.get('deploy_branches', ['main', 'master'])
        self.auto_create_webhooks = github_config.get('auto_create_webhooks', False)
        self.webhook_public_url = github_config.get('webhook_public_url', '')
        self.webhook_events = github_config.get('webhook_events', ['push', 'pull_request', 'release'])
        self.local_polling_enabled = github_config.get('local_polling_enabled', True)
        self.local_polling_interval = github_config.get('local_polling_interval', 60)
        self.local_polling_fetch = github_config.get('local_polling_fetch', False)
        self.local_polling_initial_skip = github_config.get('local_polling_initial_skip', True)
        self.local_polling_max_commits = github_config.get('local_polling_max_commits', 50)
        self.dedupe_ttl_seconds = github_config.get('dedupe_ttl_seconds', 300)
        self.patch_notes_include_diffs = github_config.get('patch_notes_include_diffs', True)
        self.patch_notes_diff_max_commits = github_config.get('patch_notes_diff_max_commits', 5)
        self.patch_notes_diff_max_lines = github_config.get('patch_notes_diff_max_lines', 120)

        # Discord notification channel
        discord_config = _get_section('discord', {})
        self.guild_id = int(discord_config.get('guild_id') or 0)
        channels_config = _get_section('channels', {})
        self.deployment_channel_id = channels_config.get('deployment_log', 0)
        self.code_fixes_channel_id = channels_config.get('code_fixes', self.deployment_channel_id)

        # Placeholder for GitHub app client if configured later
        self.github_api = None

        # Webhook server
        self.app = None
        self.runner = None
        self.site = None

        # Event handlers registry
        self.event_handlers: Dict[str, Callable] = {
            'push': self.handle_push_event,
            'pull_request': self.handle_pr_event,
            'release': self.handle_release_event,
            'workflow_run': self.handle_workflow_run_event,
        }

        # Deployment manager (will be set by bot)
        self.deployment_manager = None

        # Advanced Patch Notes Manager (will be set by bot)
        self.patch_notes_manager = None

        # AI Learning System (will be set by bot)
        self.patch_notes_trainer = None
        self.feedback_collector = None
        self.prompt_ab_testing = None
        self.prompt_auto_tuner = None

        # Patch Notes v2: Batcher + Web Exporter (will be set by bot)
        self.patch_notes_batcher = None
        self.web_exporter = None

        # Temporäre Stats vom letzten AI-Aufruf
        self._last_git_stats = None
        self._last_version = None

        # Pending webhooks queue (for when bot is not ready yet)
        self.pending_webhooks = []
        self.bot_ready = False
        self.local_polling_task = None
        self._inflight_commits: Dict[str, float] = {}
        self._ci_polling_tasks: Dict[str, asyncio.Task] = {}

        self.logger.info(f"🔧 GitHub Integration initialized (enabled: {self.enabled})")
