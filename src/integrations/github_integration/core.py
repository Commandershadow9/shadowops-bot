"""
GitHub Integration for ShadowOps Bot
Handles webhook events, auto-deployment, and Discord notifications
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Dict, Callable, Optional

from utils.state_manager import get_state_manager

from .webhook_mixin import WebhookMixin
from .polling_mixin import PollingMixin
from .event_handlers_mixin import EventHandlersMixin
from .ci_mixin import CIMixin
from .state_mixin import StateMixin
from .git_ops_mixin import GitOpsMixin
from .notifications_mixin import NotificationsMixin
from .ai_patch_notes_mixin import AIPatchNotesMixin
from .jules_workflow_mixin import JulesWorkflowMixin
from .jules_state import JulesState
from .jules_learning import JulesLearning

logger = logging.getLogger('shadowops')


def _dict_to_namespace(d: dict) -> SimpleNamespace:
    """Rekursiv dict → SimpleNamespace fuer Attribut-Zugriff (cfg.enabled etc.)."""
    ns = {}
    for k, v in d.items():
        ns[k] = _dict_to_namespace(v) if isinstance(v, dict) else v
    return SimpleNamespace(**ns)


class GitHubIntegration(JulesWorkflowMixin,
                         WebhookMixin, PollingMixin, EventHandlersMixin, CIMixin,
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

        # Jules SecOps Workflow — lazy init (async connect in _jules_startup)
        jules_raw = _get_section('jules_workflow', {})
        self._jules_enabled = bool(jules_raw.get('enabled', False))
        if self._jules_enabled:
            # Config als SimpleNamespace wrappen fuer Attribut-Zugriff (cfg.max_iterations etc.)
            jules_ns = _dict_to_namespace(jules_raw)
            # Mixin greift auf self.config.jules_workflow zu — Attribut auf Config-Objekt setzen
            if not isinstance(config, dict):
                self.config.jules_workflow = jules_ns
            self.jules_state = JulesState(self.config.security_analyst_dsn)
            self.jules_learning = JulesLearning(self.config.agent_learning_dsn)
        else:
            # Disabled Namespace damit self.config.jules_workflow.enabled = False liefert
            if not isinstance(config, dict):
                self.config.jules_workflow = SimpleNamespace(enabled=False)
            self.jules_state = None
            self.jules_learning = None
        self.redis = None  # Wird in _jules_startup gesetzt
        self._jules_started = False

        # Multi-Agent Review (Phase 3) — additive, default off
        ar_raw = _get_section('agent_review', {})
        self._agent_review_enabled = bool(ar_raw.get('enabled', False))
        self._agent_review_started = False
        self.agent_task_queue = None   # TaskQueue, wird in _agent_review_startup gesetzt
        self.jules_api_client = None   # JulesAPIClient
        self.suggestions_poller = None # JulesSuggestionsPoller
        if self._agent_review_enabled and not isinstance(config, dict):
            self.config.agent_review = _dict_to_namespace(ar_raw)
        elif not isinstance(config, dict):
            self.config.agent_review = SimpleNamespace(enabled=False)

        # Event handlers registry
        self.event_handlers: Dict[str, Callable] = {
            'push': self.handle_push_event,
            'pull_request': self._pr_dispatch,
            'release': self.handle_release_event,
            'workflow_run': self.handle_workflow_run_event,
            'issue_comment': self._comment_dispatch,
        }

        # Deployment manager (will be set by bot)
        self.deployment_manager = None

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
        self._inflight_commits: Dict[str, float] = self._load_inflight_state()
        self._ci_polling_tasks: Dict[str, asyncio.Task] = {}

        # Enterprise Hardening: Concurrency Lock + AI Circuit Breaker
        self._patch_notes_lock = asyncio.Lock()
        from utils.circuit_breaker import CircuitBreaker
        self._ai_circuit_breaker = CircuitBreaker(
            name='patch_notes_ai', threshold=5, timeout_seconds=3600,
        )

        self.logger.info(f"🔧 GitHub Integration initialized (enabled: {self.enabled}, jules: {self._jules_enabled})")

    # ── Jules Dispatch + Async Startup ────────────────────────────

    async def _jules_startup(self) -> None:
        """Async Init fuer Jules Services (DB-Pools, Redis, Stale-Lock-Recovery)."""
        if not self._jules_enabled or self._jules_started:
            return
        try:
            await self.jules_state.connect()
            await self.jules_learning.connect()

            # Redis fuer Circuit-Breaker (URL aus Config mit Auth)
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(
                self.config.redis_url, decode_responses=True
            )

            cleaned = await self.jules_state.recover_stale_locks(timeout_minutes=10)
            if cleaned:
                self.logger.warning(f"[jules] recovered {cleaned} stale locks on startup")

            self._jules_started = True
            self.logger.info("[jules] SecOps Workflow enabled — DB + Redis connected")
        except Exception:
            self.logger.exception("[jules] startup failed — disabling Jules workflow")
            self._jules_enabled = False

    async def _agent_review_startup(self) -> None:
        """Async Init fuer Agent-Review Infrastruktur (Queue + Jules API)."""
        if not self._agent_review_enabled or self._agent_review_started:
            return
        try:
            from .agent_review.queue import TaskQueue
            from .agent_review.jules_api import JulesAPIClient
            from .agent_review.suggestions_poller import JulesSuggestionsPoller

            # Queue teilt die security_analyst DB mit jules_state
            self.agent_task_queue = TaskQueue(self.config.security_analyst_dsn)
            await self.agent_task_queue.connect()

            # Jules API — Key aus jules_workflow.api_key
            api_key = getattr(self.config.jules_workflow, "api_key", None)
            if api_key:
                self.jules_api_client = JulesAPIClient(api_key=api_key)
            else:
                self.logger.warning(
                    "[agent-review] kein jules_workflow.api_key, "
                    "Scheduler kann keine Sessions starten",
                )

            # Suggestions-Poller (wenn aktiviert)
            sp_cfg = getattr(self.config.agent_review, "suggestions_poller", None)
            if sp_cfg and getattr(sp_cfg, "enabled", False) and self.jules_api_client:
                repos = getattr(sp_cfg, "repos", []) or []
                self.suggestions_poller = JulesSuggestionsPoller(
                    queue=self.agent_task_queue,
                    jules_api=self.jules_api_client,
                    repos=list(repos),
                    max_per_run=getattr(sp_cfg, "max_per_run", 20),
                )

            self._agent_review_started = True
            self.logger.info(
                "[agent-review] startup ok (queue, api_client=%s, poller=%s)",
                bool(self.jules_api_client),
                bool(self.suggestions_poller),
            )
        except Exception:
            self.logger.exception("[agent-review] startup failed — disabling")
            self._agent_review_enabled = False

    async def _pr_dispatch(self, payload: dict) -> None:
        """Dispatches PR events. Jules-Workflow laeuft zuerst, dann normaler Handler."""
        if self._jules_enabled:
            if not self._jules_started:
                await self._jules_startup()
            try:
                await self.handle_jules_pr_event(payload)
            except Exception:
                self.logger.exception("[jules] PR dispatch crashed (continuing)")
        await self.handle_pr_event(payload)

    async def _comment_dispatch(self, payload: dict) -> None:
        """Handles issue_comment events fuer manuellen /review Trigger."""
        if not self._jules_enabled:
            return
        if not self._jules_started:
            await self._jules_startup()
        try:
            action = payload.get("action")
            comment = payload.get("comment", {})
            issue = payload.get("issue", {})
            body = (comment.get("body") or "").strip()
            author = (comment.get("user", {}).get("login") or "").lower()

            # Nur exaktes /review Kommando vom Repo-Owner, nur auf PRs
            if (action == "created"
                and body.lower() == "/review"
                and "pull_request" in issue
                and author == "commandershadow9"):

                from .jules_comment import is_bot_comment
                if is_bot_comment(body):
                    return

                pr_number = issue.get("number")
                repo = (payload.get("repository") or {}).get("name", "")
                if pr_number and repo:
                    self.logger.info(
                        f"[jules] Manual /review trigger by {author} for {repo}#{pr_number}"
                    )
                    # PR-Daten holen und als synchronize-Event dispatchen
                    proc = await asyncio.create_subprocess_exec(
                        "gh", "api",
                        f"repos/Commandershadow9/{repo}/pulls/{pr_number}",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                    if proc.returncode == 0:
                        pr_data = json.loads(stdout.decode())
                        fake_payload = {
                            "action": "synchronize",
                            "pull_request": pr_data,
                            "repository": payload.get("repository"),
                        }
                        await self.handle_jules_pr_event(fake_payload)
        except Exception:
            self.logger.exception("[jules] comment dispatch crashed")
