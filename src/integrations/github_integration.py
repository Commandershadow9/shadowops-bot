"""
GitHub Integration for ShadowOps Bot
Handles webhook events, auto-deployment, and Discord notifications
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
from typing import Dict, Optional, Callable
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web
import aiohttp
import discord
from utils.state_manager import get_state_manager
from integrations.git_history_analyzer import GitHistoryAnalyzer

logger = logging.getLogger('shadowops')


class GitHubIntegration:
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

        # Pending webhooks queue (for when bot is not ready yet)
        self.pending_webhooks = []
        self.bot_ready = False
        self.local_polling_task = None
        self._inflight_commits: Dict[str, float] = {}
        self._ci_polling_tasks: Dict[str, asyncio.Task] = {}

        self.logger.info(f"🔧 GitHub Integration initialized (enabled: {self.enabled})")

    async def start_webhook_server(self):
        """Start the webhook HTTP server"""
        if not self.enabled:
            self.logger.info("ℹ️ GitHub webhooks disabled in config")
            return

        self.app = web.Application()
        self.app.router.add_post('/webhook', self.webhook_handler)
        self.app.router.add_get('/health', self.health_check)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        # Try binding to configured port; on conflict, fall back to 9090
        ports_to_try = [self.webhook_port]
        if self.webhook_port != 9090:
            ports_to_try.append(9090)

        for port in ports_to_try:
            try:
                self.site = web.TCPSite(self.runner, '0.0.0.0', port)
                await self.site.start()
                self.webhook_port = port
                self.logger.info(f"🚀 GitHub webhook server started on port {port}")
                break
            except OSError as e:
                self.logger.error(f"❌ GitHub webhook server konnte Port {port} nicht binden: {e}")
                continue
        else:
            self.logger.error("   GitHub Webhooks werden deaktiviert, bitte Port/Service prüfen.")
            self.enabled = False
            return

    async def mark_bot_ready_and_process_queue(self):
        """
        Mark bot as ready and process any pending webhooks that arrived during startup.
        Should be called by the bot after it's fully initialized.
        """
        self.bot_ready = True

        if not self.pending_webhooks:
            self.logger.info("✅ Bot marked as ready - no pending webhooks")
            return

        pending_count = len(self.pending_webhooks)
        self.logger.info(f"🔄 Bot ready - processing {pending_count} pending webhook(s)...")

        # Process all pending webhooks
        processed = 0
        failed = 0

        for webhook in self.pending_webhooks:
            try:
                event_type = webhook['event_type']
                payload = webhook['payload']
                received_at = webhook['received_at']

                self.logger.info(f"📋 Processing queued {event_type} webhook (received at {received_at})")

                # Route to appropriate handler
                handler = self.event_handlers.get(event_type)
                if handler:
                    await handler(payload)
                    processed += 1
                else:
                    self.logger.debug(f"ℹ️ No handler for queued event type: {event_type}")
                    processed += 1

            except Exception as e:
                self.logger.error(f"❌ Error processing queued webhook: {e}", exc_info=True)
                failed += 1

        # Clear the queue
        self.pending_webhooks.clear()

        self.logger.info(f"✅ Processed {processed} pending webhooks ({failed} failed)")

    async def stop_webhook_server(self):
        """Stop the webhook HTTP server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        await self.stop_local_polling()
        self.logger.info("🛑 GitHub webhook server stopped")

    async def ensure_project_webhooks(self):
        """Ensure GitHub webhooks exist for configured projects."""
        if not self.auto_create_webhooks:
            return

        github_token = self._get_github_token()
        if not github_token:
            self.logger.warning("⚠️ GitHub Token fehlt - Auto-Webhook Setup übersprungen")
            return

        if not self.webhook_public_url:
            self.logger.warning("⚠️ github.webhook_public_url fehlt - Auto-Webhook Setup übersprungen")
            return

        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        if not projects:
            return

        for project_name, project_config in projects.items():
            repo_url = project_config.get('repo_url') or project_config.get('repository_url')
            if not repo_url:
                repo_path = project_config.get('path')
                if repo_path:
                    repo_url = self._get_repo_url(Path(repo_path))
            if not repo_url:
                continue

            await self._ensure_webhook_for_repo(
                project_name=project_name,
                repo_url=repo_url,
                github_token=github_token
            )

    async def start_local_polling(self):
        """Start local git polling for push detection (fallback)."""
        if not self.local_polling_enabled:
            self.logger.info("ℹ️ Local git polling deaktiviert (config: github.local_polling_enabled=false)")
            return
        if self.local_polling_task and not self.local_polling_task.done():
            return
        self.local_polling_task = asyncio.create_task(self._local_polling_loop())
        self.logger.info(
            f"🔄 Local git polling aktiv (Interval: {self.local_polling_interval}s)"
        )

    async def stop_local_polling(self):
        """Stop local git polling task if running."""
        if not self.local_polling_task:
            return
        self.local_polling_task.cancel()
        try:
            await self.local_polling_task
        except asyncio.CancelledError:
            pass
        self.local_polling_task = None
        self.logger.info("🛑 Local git polling gestoppt")

    async def _local_polling_loop(self):
        """Background loop for local git polling."""
        while True:
            try:
                await self._poll_local_projects()
            except Exception as e:
                self.logger.error(f"❌ Local git polling Fehler: {e}", exc_info=True)
            await asyncio.sleep(self.local_polling_interval)

    async def _poll_local_projects(self):
        """Check configured local repos for new commits and send patch notes."""
        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        if not projects:
            return

        for project_name, project_config in projects.items():
            if not project_config.get('enabled', True):
                continue

            project_path = project_config.get('path')
            if not project_path:
                continue

            repo_path = Path(project_path)
            if not (repo_path / '.git').exists():
                continue

            branch = self._get_repo_branch(repo_path, project_config)
            upstream_ref = self._get_upstream_ref(repo_path)
            fallback_upstream = None
            if not upstream_ref:
                remote_url = self._run_git(repo_path, ['config', '--get', 'remote.origin.url'])
                if remote_url:
                    fallback_upstream = f"origin/{branch}"

            if self.local_polling_fetch and (upstream_ref or fallback_upstream):
                self._safe_git_fetch(repo_path)

            head_ref = upstream_ref or fallback_upstream or "HEAD"
            head_sha = self._get_commit_sha(repo_path, head_ref)
            if not head_sha:
                continue

            normalized_project = self._normalize_repo_name(project_name)
            last_sha = self._get_last_processed_commit(normalized_project, branch)
            if not last_sha and self.local_polling_initial_skip:
                self._set_last_processed_commit(normalized_project, branch, head_sha)
                self.logger.info(
                    f"ℹ️ Local git polling baseline gesetzt für {project_name}@{branch}"
                )
                continue

            if last_sha == head_sha:
                continue
            if self._is_commit_inflight(normalized_project, branch, head_sha):
                continue

            repo_url = (
                project_config.get('repo_url')
                or project_config.get('repository_url')
                or self._get_repo_url(repo_path)
            )
            commits = self._get_commits_between(repo_path, last_sha, head_ref, repo_url)
            if not commits:
                self.logger.info(
                    f"ℹ️ Keine Commits gefunden für {project_name}@{branch} (local polling)"
                )
                self._set_last_processed_commit(normalized_project, branch, head_sha)
                continue

            pusher = commits[-1]['author'].get('name', 'local')
            self.logger.info(
                f"📥 Local git update erkannt: {project_name}@{branch} ({len(commits)} Commit(s))"
            )
            if not self._reserve_commit_processing(normalized_project, branch, head_sha):
                continue
            try:
                await self._send_push_notification(
                    repo_name=normalized_project,
                    repo_url=repo_url or "",
                    branch=branch,
                    pusher=pusher,
                    commits=commits
                )
                self._set_last_processed_commit(normalized_project, branch, head_sha)
            finally:
                self._unmark_commit_inflight(normalized_project, branch, head_sha)

    def _get_github_token(self) -> Optional[str]:
        env_token = os.getenv('GITHUB_TOKEN') or os.getenv('GH_TOKEN')
        if env_token:
            return env_token
        if hasattr(self.config, 'github_token'):
            try:
                token = self.config.github_token
                if token:
                    return token
            except Exception:
                pass
        if isinstance(self.config, dict):
            return self.config.get('github', {}).get('token')
        return None

    def _parse_github_repo_slug(self, repo_url: str) -> Optional[str]:
        if not repo_url:
            return None
        url = repo_url.strip()
        if url.endswith('.git'):
            url = url[:-4]
        if url.startswith('git@'):
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host, path = remainder.split(':', 1)
                if host.endswith('github.com'):
                    return path.strip('/')
                return None
        if url.startswith('https://') or url.startswith('http://'):
            parts = url.split('/')
            if len(parts) >= 5:
                host = parts[2]
                if host.endswith('github.com'):
                    return f"{parts[3]}/{parts[4]}"
        return None

    def _get_github_api_base(self, repo_url: str) -> Optional[str]:
        if not repo_url:
            return None
        url = repo_url.strip()
        if url.startswith('git@'):
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host = remainder.split(':', 1)[0]
                if host.endswith('github.com'):
                    return "https://api.github.com"
                return f"https://{host}/api/v3"
        if url.startswith('https://') or url.startswith('http://'):
            host = url.split('/')[2]
            if host.endswith('github.com'):
                return "https://api.github.com"
            return f"https://{host}/api/v3"
        return None

    async def _ensure_webhook_for_repo(self, project_name: str, repo_url: str, github_token: str) -> None:
        repo_slug = self._parse_github_repo_slug(repo_url)
        if not repo_slug:
            self.logger.warning(f"⚠️ Repo URL nicht GitHub-kompatibel: {repo_url}")
            return

        api_base = self._get_github_api_base(repo_url)
        if not api_base:
            self.logger.warning(f"⚠️ Konnte GitHub API Base nicht bestimmen: {repo_url}")
            return

        hooks_url = f"{api_base}/repos/{repo_slug}/hooks"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {github_token}"
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(hooks_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Webhook-Check fehlgeschlagen ({resp.status}) für {repo_slug}: {body}"
                        )
                        return
                    hooks = await resp.json()
            except Exception as e:
                self.logger.warning(f"⚠️ Webhook-Check Fehler für {repo_slug}: {e}")
                return

            for hook in hooks:
                config = hook.get('config', {})
                if config.get('url') == self.webhook_public_url:
                    self.logger.info(f"✅ Webhook existiert bereits für {project_name}")
                    return

            payload = {
                "name": "web",
                "active": True,
                "events": self.webhook_events,
                "config": {
                    "url": self.webhook_public_url,
                    "content_type": "json"
                }
            }

            if self.webhook_secret:
                payload["config"]["secret"] = self.webhook_secret

            try:
                async with session.post(hooks_url, json=payload, timeout=20) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Webhook-Erstellung fehlgeschlagen ({resp.status}) für {repo_slug}: {body}"
                        )
                        return
                    self.logger.info(f"✅ Webhook erstellt für {project_name}")
            except Exception as e:
                self.logger.warning(f"⚠️ Webhook-Erstellung Fehler für {repo_slug}: {e}")

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'service': 'github-webhook',
            'timestamp': datetime.utcnow().isoformat()
        })

    async def webhook_handler(self, request: web.Request) -> web.Response:
        """
        Handle incoming GitHub webhook requests

        Verifies HMAC signature and routes to appropriate handler
        """
        try:
            # Read request body
            body = await request.read()

            # Verify signature
            if self.webhook_secret:
                signature = request.headers.get('X-Hub-Signature-256', '')
                if not self._verify_signature(body, signature):
                    self.logger.warning("⚠️ Invalid webhook signature")
                    return web.Response(status=401, text="Invalid signature")

            # Parse payload
            payload = json.loads(body)
            event_type = request.headers.get('X-GitHub-Event', 'unknown')

            self.logger.info(f"📥 Received GitHub event: {event_type}")

            # If bot is not ready yet, queue the webhook for later processing
            if not self.bot_ready:
                self.pending_webhooks.append({
                    'event_type': event_type,
                    'payload': payload,
                    'received_at': datetime.now().isoformat()
                })
                self.logger.info(f"📋 Bot not ready yet - queued {event_type} webhook ({len(self.pending_webhooks)} pending)")
                return web.Response(status=202, text="Accepted - queued for processing")

            # Route to appropriate handler
            handler = self.event_handlers.get(event_type)
            if handler:
                await handler(payload)
            else:
                self.logger.debug(f"ℹ️ No handler for event type: {event_type}")

            return web.Response(status=200, text="OK")

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook: {e}", exc_info=True)
            return web.Response(status=500, text=f"Error: {str(e)}")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """
        Verify GitHub webhook HMAC signature

        Args:
            body: Request body bytes
            signature: X-Hub-Signature-256 header value

        Returns:
            True if signature is valid
        """
        if not signature.startswith('sha256='):
            return False

        expected_signature = signature.split('=')[1]

        mac = hmac.new(
            self.webhook_secret.encode('utf-8'),
            msg=body,
            digestmod=hashlib.sha256
        )
        calculated_signature = mac.hexdigest()

        return hmac.compare_digest(calculated_signature, expected_signature)

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Public wrapper for webhook signature verification."""
        return self._verify_signature(body, signature)

    async def handle_push_event(self, payload: Dict):
        """
        Handle push events from GitHub, creating detailed patch notes.
        """
        try:
            repo_name = payload['repository']['name']
            repo_url = payload['repository']['html_url']
            ref = payload['ref']
            branch = ref.split('/')[-1]
            pusher = payload['pusher']['name']
            
            # Don't process pushes with no commits (e.g., branch creation)
            commits = payload.get('commits', [])
            if not commits or payload.get('created', False) and payload.get('head_commit') is None:
                self.logger.info(f"Skipping push event for {repo_name}/{branch} (no commits).")
                return

            head_commit = payload.get('head_commit', {}).get('id')
            if not head_commit and commits:
                head_commit = commits[-1].get('id')
            normalized_repo = self._normalize_repo_name(repo_name)
            if head_commit and not self._reserve_commit_processing(normalized_repo, branch, head_commit):
                self.logger.info(
                    f"ℹ️ Push für {repo_name}/{branch} bereits verarbeitet ({head_commit[:7]})"
                )
                return

            self.logger.info(
                f"📌 Push to {repo_name}/{branch}: "
                f"{len(commits)} commit(s) by {pusher}"
            )

            # Send detailed patch notes notification
            try:
                await self._send_push_notification(
                    repo_name=normalized_repo,
                    repo_url=repo_url,
                    branch=branch,
                    pusher=pusher,
                    commits=commits
                )
                if head_commit:
                    self._set_last_processed_commit(normalized_repo, branch, head_commit)
            finally:
                if head_commit:
                    self._unmark_commit_inflight(normalized_repo, branch, head_commit)

            # Auto-deploy if enabled and on a deployment branch
            # Assuming 'head_commit' is present if there are commits
            commit_sha = payload.get('head_commit', {}).get('id', 'unknown')[:7]
            if self.auto_deploy_enabled and branch in self.deploy_branches:
                self.logger.info(f"🚀 Triggering auto-deploy for {repo_name}/{branch}")
                await self._trigger_deployment(repo_name, branch, commit_sha)

        except Exception as e:
            self.logger.error(f"❌ Error handling push event: {e}", exc_info=True)

    async def handle_pr_event(self, payload: Dict):
        """Handle pull request events from GitHub"""
        try:
            action = payload['action']  # opened, closed, synchronize, etc.
            pr = payload['pull_request']

            repo_name = payload['repository']['name']
            pr_number = pr['number']
            pr_title = pr['title']
            pr_author = pr['user']['login']
            pr_url = pr['html_url']
            source_branch = pr['head']['ref']
            target_branch = pr['base']['ref']

            self.logger.info(
                f"🔀 PR #{pr_number} {action} in {repo_name}: "
                f"{source_branch} → {target_branch}"
            )

            # Send Discord notification
            await self._send_pr_notification(
                action, repo_name, pr_number, pr_title,
                pr_author, source_branch, target_branch, pr_url
            )

            # If PR was merged to deployment branch, trigger deploy
            if action == 'closed' and pr.get('merged', False):
                if target_branch in self.deploy_branches and self.auto_deploy_enabled:
                    merge_commit_sha = pr['merge_commit_sha'][:7]
                    self.logger.info(
                        f"🚀 PR merged to {target_branch}, triggering deployment"
                    )
                    await self._trigger_deployment(repo_name, target_branch, merge_commit_sha)

        except Exception as e:
            self.logger.error(f"❌ Error handling PR event: {e}", exc_info=True)

    async def handle_pull_request_event(self, payload: Dict):
        """
        Compatibility wrapper for handling pull request events.

        GitHub sends the event type as `pull_request`, which is routed to
        `handle_pr_event`. This wrapper keeps the name explicit for tests and
        future callers.
        """
        await self.handle_pr_event(payload)

    async def handle_release_event(self, payload: Dict):
        """Handle release events from GitHub"""
        try:
            action = payload['action']  # published, created, deleted, etc.
            release = payload['release']

            repo_name = payload['repository']['name']
            tag_name = release['tag_name']
            release_name = release['name'] or tag_name
            release_author = release['author']['login']
            release_url = release['html_url']
            is_prerelease = release['prerelease']

            self.logger.info(
                f"🏷️ Release {action} in {repo_name}: "
                f"{tag_name} ({'prerelease' if is_prerelease else 'stable'})"
            )

            # Send Discord notification
            await self._send_release_notification(
                action, repo_name, tag_name, release_name,
                release_author, is_prerelease, release_url
            )

        except Exception as e:
            self.logger.error(f"❌ Error handling release event: {e}", exc_info=True)

    async def handle_workflow_run_event(self, payload: Dict):
        """Handle workflow_run events (CI Ergebnisse)."""
        try:
            workflow = payload.get('workflow_run', {}) or {}
            repo = payload.get('repository', {}) or {}
            action = payload.get('action') or workflow.get('action') or ''
            from_poll = payload.get('_from_poll', False)

            repo_name = repo.get('name', 'unknown')
            repo_url = repo.get('html_url')
            run_name = workflow.get('name', 'CI')
            run_path = workflow.get('path') or ''
            conclusion = workflow.get('conclusion') or payload.get('conclusion') or 'unknown'
            status = workflow.get('status') or payload.get('status') or 'unknown'
            branch = workflow.get('head_branch') or payload.get('branch') or '-'
            sha = (workflow.get('head_sha') or payload.get('sha') or '')[:7]
            run_url = workflow.get('html_url') or payload.get('url')
            run_number = workflow.get('run_number')
            run_started_at = workflow.get('run_started_at')
            updated_at = workflow.get('updated_at') or workflow.get('completed_at')
            head_commit = workflow.get('head_commit') or {}
            actor_login = (workflow.get('actor') or {}).get('login')
            event_name = workflow.get('event')
            summary = payload.get('summary')
            failed_jobs = payload.get('failed_jobs') or []
            jobs_url = workflow.get('jobs_url')
            run_id = workflow.get('id') or workflow.get('run_id') or run_number or sha or 'unknown'
            run_api_url = workflow.get('url')
            jobs = []
            jobs_summary = None
            job_details = []
            failed_steps_summary = []
            steps_total = 0
            steps_failed = 0
            steps_skipped = 0
            steps_completed = 0
            jobs_completed = 0
            jobs_total = 0
            active_job_name = None
            active_step_name = None
            is_completed = action == 'completed' or status == 'completed'

            if not summary:
                if is_completed:
                    summary = 'Alle Jobs erfolgreich.' if conclusion == 'success' else 'CI fehlgeschlagen.'
                else:
                    summary = 'CI laeuft...'

            # Project config lookup (case-insensitive)
            project_config = {}
            for key in self.config.projects.keys():
                if key.lower() == repo_name.lower():
                    project_config = self.config.projects[key]
                    break

            allowed_workflows = project_config.get('ci_workflows')
            if allowed_workflows:
                allowed = False
                for workflow_name in allowed_workflows:
                    name_lower = str(workflow_name).lower()
                    if name_lower and (
                        name_lower == str(run_name).lower()
                        or name_lower == str(run_path).lower()
                        or name_lower in str(run_name).lower()
                        or name_lower in str(run_path).lower()
                    ):
                        allowed = True
                        break
                if not allowed:
                    self.logger.info(
                        f"ℹ️ Ignoriere workflow_run '{run_name}' (nicht in ci_workflows erlaubt)."
                    )
                    return
            else:
                if 'notify' in str(run_name).lower() or 'ci-notify' in str(run_path).lower():
                    self.logger.info(
                        f"ℹ️ Ignoriere workflow_run '{run_name}' (Notification Workflow)."
                    )
                    return

            allow_jobs_fetch = jobs_url and (is_completed or status in ('in_progress', 'queued'))
            if allow_jobs_fetch:
                jobs_response = await self._fetch_workflow_jobs(jobs_url)
                if jobs_response and isinstance(jobs_response, dict):
                    jobs = jobs_response.get('jobs') or []

            if jobs:
                jobs_total = len(jobs)
                counts = {
                    'success': 0,
                    'failure': 0,
                    'cancelled': 0,
                    'skipped': 0,
                    'neutral': 0,
                    'timed_out': 0,
                    'action_required': 0,
                    'unknown': 0,
                }

                for job in jobs:
                    job_conclusion = job.get('conclusion') or job.get('status') or 'unknown'
                    job_status = job.get('status') or 'unknown'
                    if job_status == 'completed':
                        jobs_completed += 1
                    if job_conclusion in counts:
                        counts[job_conclusion] += 1
                    else:
                        counts['unknown'] += 1

                    if job_conclusion not in ('success', 'skipped'):
                        job_name = job.get('name', 'Unbekannter Job')
                        if job_name not in failed_jobs:
                            failed_jobs.append(job_name)

                    emoji = '✅'
                    if job_conclusion in ('failure', 'timed_out'):
                        emoji = '❌'
                    elif job_conclusion in ('cancelled', 'action_required'):
                        emoji = '⚠️'
                    elif job_conclusion == 'skipped':
                        emoji = '⏭️'

                    job_name = job.get('name', 'Unbekannter Job')
                    job_url = job.get('html_url') or run_url or ''
                    if job_url:
                        job_details.append(f"{emoji} [{job_name}]({job_url}) — {job_conclusion}")
                    else:
                        job_details.append(f"{emoji} {job_name} — {job_conclusion}")

                    steps = job.get('steps') or []
                    failed_steps = []
                    for step in steps:
                        step_status = step.get('status') or step.get('conclusion') or 'unknown'
                        step_conclusion = step.get('conclusion') or step.get('status') or 'unknown'
                        steps_total += 1
                        if step_status == 'completed' or step_conclusion in ('success', 'failure', 'skipped', 'cancelled', 'timed_out', 'action_required'):
                            steps_completed += 1
                        if step_conclusion == 'skipped':
                            steps_skipped += 1
                        if step_conclusion not in ('success', 'skipped'):
                            steps_failed += 1
                            failed_steps.append(step.get('name', 'Unbekannter Schritt'))
                        if not is_completed and step_status == 'in_progress' and not active_step_name:
                            active_step_name = step.get('name', 'Unbekannter Schritt')

                    if not is_completed and job_status == 'in_progress' and not active_job_name:
                        active_job_name = job_name

                    if failed_steps:
                        limited_steps = failed_steps[:4]
                        suffix = '' if len(failed_steps) <= 4 else '…'
                        failed_steps_summary.append(f"{job_name}: {', '.join(limited_steps)}{suffix}")

                total_jobs = len(jobs)
                jobs_summary = (
                    f"Jobs: {total_jobs} | ✅ {counts['success']} | ❌ {counts['failure']} | "
                    f"⚠️ {counts['cancelled'] + counts['action_required']} | ⏭️ {counts['skipped']}"
                )
                if steps_total:
                    jobs_summary = f"{jobs_summary}\nSchritte: {steps_total} | ❌ {steps_failed} | ⏭️ {steps_skipped}"

            project_color = project_config.get('color', 0x3498DB)
            if conclusion == 'success':
                project_color = 0x2ECC71
            elif conclusion == 'failure':
                project_color = 0xE74C3C
            elif conclusion == 'cancelled':
                project_color = 0xF1C40F

            def _parse_ts(value: Optional[str]) -> Optional[datetime]:
                if not value:
                    return None
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    return None

            duration_text = None
            started_dt = _parse_ts(run_started_at)
            if started_dt:
                end_dt = _parse_ts(updated_at) if is_completed else datetime.now(timezone.utc)
                if end_dt:
                    delta = end_dt - started_dt
                    seconds = max(int(delta.total_seconds()), 0)
                    minutes, secs = divmod(seconds, 60)
                    hours, minutes = divmod(minutes, 60)
                    if hours:
                        duration_text = f"{hours}h {minutes}m {secs}s"
                    elif minutes:
                        duration_text = f"{minutes}m {secs}s"
                    else:
                        duration_text = f"{secs}s"

            title = f"🧪 CI Ergebnis: {run_name}"
            if run_number:
                title = f"{title} #{run_number}"

            embed = discord.Embed(
                title=title,
                url=run_url,
                color=project_color,
                timestamp=datetime.utcnow(),
                description=summary,
            )
            embed.add_field(name="Repository", value=repo_name, inline=True)
            embed.add_field(name="Branch", value=branch, inline=True)
            embed.add_field(name="Commit", value=sha or '-', inline=True)
            embed.add_field(name="Status", value=status, inline=True)
            embed.add_field(name="Ergebnis", value=conclusion, inline=True)
            if event_name:
                embed.add_field(name="Trigger", value=event_name, inline=True)
            if actor_login:
                embed.add_field(name="Actor", value=actor_login, inline=True)
            if duration_text:
                embed.add_field(name="Dauer", value=duration_text, inline=True)
            if run_path:
                embed.add_field(name="Workflow Datei", value=run_path, inline=False)
            if head_commit.get('message'):
                commit_line = str(head_commit.get('message')).splitlines()[0][:200]
                embed.add_field(name="Commit-Message", value=commit_line, inline=False)

            if jobs_summary:
                embed.add_field(name="Tests/Jobs", value=jobs_summary, inline=False)

            if not is_completed:
                if jobs_total and steps_total:
                    embed.add_field(
                        name="Fortschritt",
                        value=f"Jobs: {jobs_completed}/{jobs_total} | Schritte: {steps_completed}/{steps_total}",
                        inline=False,
                    )
                if active_job_name:
                    embed.add_field(name="Aktueller Job", value=active_job_name, inline=True)
                if active_step_name:
                    embed.add_field(name="Aktueller Schritt", value=active_step_name, inline=True)

            detail_embeds = []
            if job_details:
                details_text = "\n".join(job_details)
                if len(details_text) <= 950:
                    embed.add_field(name="Job-Details", value=details_text, inline=False)
                else:
                    chunk = []
                    chunk_len = 0
                    chunks = []
                    for line in job_details:
                        line_len = len(line) + 1
                        if chunk_len + line_len > 900 and chunk:
                            chunks.append("\n".join(chunk))
                            chunk = []
                            chunk_len = 0
                        chunk.append(line)
                        chunk_len += line_len
                    if chunk:
                        chunks.append("\n".join(chunk))

                    total_parts = len(chunks)
                    for index, text in enumerate(chunks, start=1):
                        detail_embed = discord.Embed(
                            title=f"🧪 CI Job-Details ({index}/{total_parts})",
                            url=run_url,
                            color=project_color,
                            timestamp=datetime.utcnow(),
                        )
                        detail_embed.add_field(name="Job-Details", value=text, inline=False)
                        detail_embeds.append(detail_embed)
            elif jobs_url and is_completed:
                embed.add_field(
                    name="Job-Details",
                    value="Nicht abrufbar (GitHub Token/Rate-Limit oder Repo privat). "
                          "Optional: github.token in der Bot-Config setzen.",
                    inline=False,
                )

            if failed_jobs:
                failed_text = "\n".join(f"• {job}" for job in failed_jobs)
                if len(failed_text) > 1000:
                    failed_text = failed_text[:1000] + "…"
                embed.add_field(name="Fehlgeschlagene Jobs", value=failed_text, inline=False)

            if failed_steps_summary:
                failed_steps_text = "\n".join(f"• {entry}" for entry in failed_steps_summary)
                if len(failed_steps_text) > 1000:
                    failed_steps_text = failed_steps_text[:1000] + "…"
                embed.add_field(name="Fehlgeschlagene Schritte", value=failed_steps_text, inline=False)

            run_key_base = f"{repo_name}:{run_id}"

            # Always send to internal deployment log
            internal_channel = self.bot.get_channel(self.deployment_channel_id)
            if internal_channel:
                await self._send_or_update_ci_message(
                    channel=internal_channel,
                    embed=embed,
                    run_key=run_key_base,
                    allow_update=True,
                )
                for item in detail_embeds:
                    await internal_channel.send(embed=item)
            else:
                self.logger.warning("⚠️ Deployment log channel nicht gefunden (CI Notification).")

            # Optional project-specific CI channel
            ci_channel_id = project_config.get('ci_channel_id')
            if ci_channel_id:
                ci_channel = self.bot.get_channel(ci_channel_id)
                if ci_channel and ci_channel_id != self.deployment_channel_id:
                    await self._send_or_update_ci_message(
                        channel=ci_channel,
                        embed=embed,
                        run_key=run_key_base,
                        allow_update=True,
                    )
                    for item in detail_embeds:
                        await ci_channel.send(embed=item)

            if not from_poll:
                if is_completed:
                    self._cancel_ci_polling(run_key_base)
                else:
                    await self._ensure_ci_polling(
                        run_key=run_key_base,
                        repo=repo,
                        run_api_url=run_api_url,
                    )

        except Exception as e:
            self.logger.error(f"❌ Error handling workflow_run event: {e}", exc_info=True)

    async def _send_or_update_ci_message(
        self,
        channel: discord.abc.Messageable,
        embed: discord.Embed,
        run_key: str,
        allow_update: bool,
    ) -> None:
        """Send or update a CI notification message for a workflow run."""
        if not self.guild_id or not run_key:
            await channel.send(embed=embed)
            return

        state_key = 'ci_messages'
        ci_messages = self.state_manager.get_value(self.guild_id, state_key, {})
        channel_id = getattr(channel, 'id', None)
        if channel_id is None:
            await channel.send(embed=embed)
            return

        entry = ci_messages.get(run_key, {})
        message_id = entry.get(str(channel_id))

        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(embed=embed)
                return
            except Exception as e:
                self.logger.warning(f"⚠️ Konnte CI-Nachricht nicht aktualisieren: {e}")

        sent = await channel.send(embed=embed)
        entry[str(channel_id)] = sent.id
        ci_messages[run_key] = entry
        self.state_manager.set_value(self.guild_id, state_key, ci_messages)

    async def _ensure_ci_polling(self, run_key: str, repo: Dict, run_api_url: Optional[str]) -> None:
        """Start polling for CI updates (every 60s) until completed."""
        if not run_key:
            return
        existing = self._ci_polling_tasks.get(run_key)
        if existing and not existing.done():
            return

        task = asyncio.create_task(self._poll_ci_run(run_key, repo, run_api_url))
        self._ci_polling_tasks[run_key] = task

    def _cancel_ci_polling(self, run_key: str) -> None:
        task = self._ci_polling_tasks.pop(run_key, None)
        if task and not task.done():
            task.cancel()

    async def _poll_ci_run(self, run_key: str, repo: Dict, run_api_url: Optional[str]) -> None:
        """Poll workflow_run status and refresh the CI message."""
        attempts = 0
        max_attempts = 120  # ~2 hours
        try:
            while attempts < max_attempts:
                await asyncio.sleep(60)
                attempts += 1

                if not run_api_url:
                    continue

                workflow = await self._fetch_workflow_run(run_api_url)
                if not workflow:
                    continue

                status = workflow.get('status') or 'unknown'
                action = 'completed' if status == 'completed' else 'in_progress'
                payload = {
                    'workflow_run': workflow,
                    'repository': repo,
                    'action': action,
                    '_from_poll': True,
                }
                await self.handle_workflow_run_event(payload)

                if status == 'completed':
                    break
        except asyncio.CancelledError:
            return
        finally:
            self._ci_polling_tasks.pop(run_key, None)

    async def _fetch_workflow_jobs(self, jobs_url: str) -> Optional[Dict]:
        """Fetch job details for a workflow run."""
        if not jobs_url:
            return None

        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(jobs_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Workflow Jobs konnten nicht geladen werden ({resp.status}): {body}"
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Laden der Workflow Jobs: {e}", exc_info=True)
            return None

    async def _fetch_workflow_run(self, run_api_url: Optional[str]) -> Optional[Dict]:
        """Fetch workflow_run details from GitHub API."""
        if not run_api_url:
            return None
        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = self._get_github_token()
        if token:
            headers["Authorization"] = f"token {token}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(run_api_url, timeout=20) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.logger.warning(
                            f"⚠️ Workflow Run konnte nicht geladen werden ({resp.status}): {body}"
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Laden des Workflow Runs: {e}", exc_info=True)
            return None

    async def _trigger_deployment(self, repo_name: str, branch: str, commit_sha: str):
        """
        Trigger deployment for a repository

        Args:
            repo_name: Name of the repository
            branch: Branch to deploy
            commit_sha: Commit SHA being deployed
        """
        if not self.deployment_manager:
            self.logger.warning("⚠️ No deployment manager configured")
            return

        try:
            self.logger.info(f"🚀 Starting deployment: {repo_name}@{commit_sha}")

            # Notify Discord that deployment is starting
            channel = self.bot.get_channel(self.deployment_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🚀 Deployment Started",
                    description=f"Deploying **{repo_name}** from `{branch}@{commit_sha}`",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Repository", value=repo_name, inline=True)
                embed.add_field(name="Branch", value=branch, inline=True)
                embed.add_field(name="Commit", value=commit_sha, inline=True)
                await channel.send(embed=embed)

            # Execute deployment
            result = await self.deployment_manager.deploy_project(repo_name, branch)

            # Send result notification
            if result['success']:
                await self._send_deployment_success(repo_name, branch, commit_sha, result)
            else:
                await self._send_deployment_failure(repo_name, branch, commit_sha, result)

        except Exception as e:
            self.logger.error(f"❌ Deployment failed: {e}", exc_info=True)
            await self._send_deployment_error(repo_name, branch, commit_sha, str(e))

    async def _send_push_notification(
        self, repo_name: str, repo_url: str, branch: str, pusher: str, commits: list
    ):
        """Send detailed Discord notification for a push event."""
        # Find project config to get color and potential customer channel (case-insensitive)
        project_config = {}
        project_config_key = repo_name

        # Try case-insensitive lookup for project config
        for key in self.config.projects.keys():
            if key.lower() == repo_name.lower():
                project_config = self.config.projects[key]
                project_config_key = key
                break

        if not project_config:
            project_config = self.config.projects.get(repo_name, {})

        project_color = project_config.get('color', 0x3498DB) # Default blue

        # === INTERNAL EMBED (Technical, for developers) - DEUTSCH ===
        commits_url = f"{repo_url}/commits/{branch}" if repo_url else None
        internal_embed = discord.Embed(
            title=f"🚀 Code-Update: {repo_name}",
            url=commits_url,
            color=project_color,
            timestamp=datetime.utcnow()
        )
        internal_embed.set_author(name=pusher)
        internal_embed.add_field(name="Branch", value=branch, inline=True)
        internal_embed.add_field(name="Commits", value=str(len(commits)), inline=True)

        commit_details = []
        for commit in commits:
            sha = commit['id'][:7]
            author = commit['author']['name']
            message = commit['message'].split('\n')[0] # First line of commit message
            url = commit['url']
            if url:
                commit_details.append(f"[`{sha}`]({url}) {message} - *{author}*")
            else:
                commit_details.append(f"`{sha}` {message} - *{author}*")

        if commit_details:
            internal_embed.description = "\n".join(commit_details)
        else:
            internal_embed.description = "Keine neuen Commits in diesem Push."

        # === CUSTOMER EMBED (User-friendly, categorized) - Language from config ===
        patch_config = project_config.get('patch_notes', {})
        language = patch_config.get('language', 'de')  # Default: Deutsch

        # Language-specific texts
        if language == 'en':
            title_text = f"✨ Updates for {repo_name}"
            footer_text = f"{len(commits)} commit(s) by {pusher}"
            feature_header = "**🆕 New Features:**"
            bugfix_header = "**🐛 Bug Fixes:**"
            improvement_header = "**⚡ Improvements:**"
            other_header = "**📝 Other Changes:**"
            default_desc = "Various updates and improvements"
        else:  # Deutsch
            title_text = f"✨ Updates für {repo_name}"
            footer_text = f"{len(commits)} Commit(s) von {pusher}"
            feature_header = "**🆕 Neue Features:**"
            bugfix_header = "**🐛 Bugfixes:**"
            improvement_header = "**⚡ Verbesserungen:**"
            other_header = "**📝 Weitere Änderungen:**"
            default_desc = "Diverse Updates und Verbesserungen"

        customer_embed = discord.Embed(
            title=title_text,
            url=commits_url,
            color=project_color,
            timestamp=datetime.utcnow()
        )

        # Categorize commits by type
        features = []
        fixes = []
        improvements = []
        other = []

        for commit in commits:
            message = commit['message'].split('\n')[0]
            message_lower = message.lower()

            # Simple categorization based on commit message
            if message_lower.startswith('feat') or 'feature' in message_lower or 'add' in message_lower:
                features.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('fix') or 'bug' in message_lower or 'issue' in message_lower:
                fixes.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('improve') or 'optimize' in message_lower or 'enhance' in message_lower or 'update' in message_lower:
                improvements.append(self._format_user_friendly_commit(message))
            else:
                other.append(self._format_user_friendly_commit(message))

        # Build customer-friendly description
        description_parts = []

        if features:
            description_parts.append(feature_header + "\n" + "\n".join(f"• {f}" for f in features))

        if fixes:
            description_parts.append(bugfix_header + "\n" + "\n".join(f"• {f}" for f in fixes))

        if improvements:
            description_parts.append(improvement_header + "\n" + "\n".join(f"• {i}" for i in improvements))

        if other:
            description_parts.append(other_header + "\n" + "\n".join(f"• {o}" for o in other))

        customer_embed.description = "\n\n".join(description_parts) if description_parts else default_desc

        customer_embed.set_footer(text=footer_text)

        # === ADVANCED PATCH NOTES SYSTEM (if available) ===
        # Try advanced system first (CHANGELOG-based, review system)
        if self.patch_notes_manager and patch_config.get('use_advanced_system', False):
            try:
                self.logger.info(f"🎯 Using advanced patch notes system for {repo_name}")
                await self.patch_notes_manager.handle_git_push(
                    project_name=repo_name,
                    project_config=project_config,
                    commits=commits,
                    repo_name=repo_name
                )
                # Advanced system handles everything - skip old logic
                return
            except Exception as e:
                self.logger.warning(f"⚠️ Advanced system failed, falling back to legacy: {e}", exc_info=True)

        # === AI-GENERATED PATCH NOTES (legacy system) ===
        use_ai = patch_config.get('use_ai', False)
        language = patch_config.get('language', 'de')

        ai_description = None
        if use_ai and self.ai_service:
            try:
                self.logger.info(f"🤖 Generiere KI Patch Notes für {repo_name} (Sprache: {language})...")
                ai_description = await self._generate_ai_patch_notes(commits, language, repo_name, project_config)
                if ai_description:
                    customer_embed.description = ai_description
                    self.logger.info(f"✅ KI Patch Notes erfolgreich generiert")
            except Exception as e:
                self.logger.warning(f"⚠️ KI Patch Notes Generierung fehlgeschlagen, verwende Fallback: {e}")
                # Keep the categorized version as fallback

        if not ai_description:
            changelog_fallback = self._build_changelog_fallback_description(project_config, language)
            if changelog_fallback:
                customer_embed.description = changelog_fallback

        # 1. Send to internal channel (technical embed)
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if internal_channel:
            try:
                # Check if description is too long and split if needed
                description_chunks = self._split_embed_description(internal_embed.description or "")

                if len(description_chunks) <= 1:
                    # Single embed - send as is
                    await internal_channel.send(embed=internal_embed)
                    self.logger.info(f"📢 Technische Patch Notes für {repo_name} im internen Channel gesendet.")
                else:
                    # Multiple embeds needed - split across messages
                    for i, chunk in enumerate(description_chunks):
                        embed_copy = discord.Embed(
                            title=f"{internal_embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else internal_embed.title,
                            url=internal_embed.url,
                            color=internal_embed.color,
                            description=chunk,
                            timestamp=internal_embed.timestamp
                        )
                        if i == 0:
                            embed_copy.set_author(name=internal_embed.author.name)
                            # Copy fields for first embed
                            for field in internal_embed.fields:
                                embed_copy.add_field(name=field.name, value=field.value, inline=field.inline)
                        await internal_channel.send(embed=embed_copy)

                    self.logger.info(f"📢 Technische Patch Notes für {repo_name} im internen Channel gesendet ({len(description_chunks)} Teile).")
            except Exception as e:
                self.logger.error(f"❌ Fehler beim Senden der Push-Benachrichtigung im internen Channel: {e}")

        # 2. Send to customer-facing channel (user-friendly embed)
        # Extract version from commits for feedback tracking (do this BEFORE sending messages)
        version = None
        import re
        for commit in commits:
            msg = commit.get('message', '')
            match = re.search(r'v?(?:ersion|elease)?\s*([0-9]+\.[0-9]+\.[0-9]+)', msg, re.IGNORECASE)
            if match:
                version = match.group(1)
                self.logger.info(f"📌 Version detected from commits: v{version}")
                break

        customer_channel_id = project_config.get('update_channel_id')
        if customer_channel_id:
            customer_channel = self.bot.get_channel(customer_channel_id)
            if customer_channel:
                try:
                    # Check if description is too long and split if needed
                    description_chunks = self._split_embed_description(customer_embed.description or "")

                    sent_message = None

                    if len(description_chunks) <= 1:
                        # Single embed - send as is
                        sent_message = await customer_channel.send(embed=customer_embed)
                        self.logger.info(f"📢 Benutzerfreundliche Patch Notes für {repo_name} im Kunden-Channel {customer_channel_id} gesendet.")
                    else:
                        # Multiple embeds needed - split across messages
                        for i, chunk in enumerate(description_chunks):
                            embed_copy = discord.Embed(
                                title=f"{customer_embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else customer_embed.title,
                                url=customer_embed.url,
                                color=customer_embed.color,
                                description=chunk,
                                timestamp=customer_embed.timestamp
                            )
                            if i == len(description_chunks) - 1:  # Add footer only to last embed
                                embed_copy.set_footer(text=customer_embed.footer.text)
                            message = await customer_channel.send(embed=embed_copy)

                            # Track the first message for feedback (main content)
                            if i == 0:
                                sent_message = message

                        self.logger.info(f"📢 Benutzerfreundliche Patch Notes für {repo_name} im Kunden-Channel {customer_channel_id} gesendet ({len(description_chunks)} Teile).")

                    # 🎯 ACTIVATE FEEDBACK COLLECTION for internal channel message
                    if sent_message and self.feedback_collector and version:
                        try:
                            await self.feedback_collector.track_patch_notes_message(
                                message=sent_message,
                                project=repo_name,
                                version=version
                            )
                            self.logger.info(f"👍 Feedback collection activated for {repo_name} v{version} (internal channel)")
                        except Exception as e:
                            self.logger.warning(f"⚠️ Could not activate feedback collection for internal channel: {e}")

                except Exception as e:
                    self.logger.error(f"❌ Fehler beim Senden der Push-Benachrichtigung im Kunden-Channel {customer_channel_id}: {e}")
            else:
                self.logger.warning(f"⚠️ Kunden-Update Channel {customer_channel_id} für {repo_name} nicht gefunden.")

        # 3. Send to external notification channels (customer servers) WITH feedback collection
        await self._send_external_git_notifications(repo_name, customer_embed, project_config, version)

    def _get_guild_id(self) -> int:
        """Resolve guild id from config in a safe way."""
        try:
            return int(getattr(self.config, 'guild_id'))
        except Exception:
            pass

        if isinstance(self.config, dict):
            return int(self.config.get('discord', {}).get('guild_id', 0))

        discord_cfg = getattr(self.config, 'discord', {}) or {}
        return int(discord_cfg.get('guild_id', 0))

    def _get_git_state(self) -> Dict[str, str]:
        guild_id = self._get_guild_id()
        state = self.state_manager.get_value(guild_id, 'git_push_state', {})
        return state if isinstance(state, dict) else {}

    def _set_git_state(self, state: Dict[str, str]) -> None:
        guild_id = self._get_guild_id()
        self.state_manager.set_value(guild_id, 'git_push_state', state)

    def _git_state_key(self, repo_name: str, branch: str) -> str:
        normalized = self._normalize_repo_name(repo_name)
        return f"{normalized}:{branch}"

    def _get_last_processed_commit(self, repo_name: str, branch: str) -> Optional[str]:
        state = self._get_git_state()
        primary_key = self._git_state_key(repo_name, branch)
        if primary_key in state:
            return state.get(primary_key)

        # Backward-compat keys (case variants before normalization)
        legacy_keys = {
            f"{repo_name}:{branch}",
            f"{repo_name.lower()}:{branch}",
            f"{repo_name.upper()}:{branch}",
        }
        for key in legacy_keys:
            if key in state:
                return state.get(key)
        return None

    def _set_last_processed_commit(self, repo_name: str, branch: str, commit_sha: str) -> None:
        state = self._get_git_state()
        state[self._git_state_key(repo_name, branch)] = commit_sha
        self._set_git_state(state)

    def _is_duplicate_push(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        return self._get_last_processed_commit(repo_name, branch) == commit_sha

    def _commit_key(self, repo_name: str, branch: str, commit_sha: str) -> str:
        normalized = self._normalize_repo_name(repo_name)
        return f"{normalized}:{branch}:{commit_sha}"

    def _cleanup_inflight(self) -> None:
        if not self._inflight_commits:
            return
        now = datetime.utcnow().timestamp()
        expired = [
            key for key, ts in self._inflight_commits.items()
            if now - ts > self.dedupe_ttl_seconds
        ]
        for key in expired:
            self._inflight_commits.pop(key, None)

    def _is_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        self._cleanup_inflight()
        key = self._commit_key(repo_name, branch, commit_sha)
        return key in self._inflight_commits

    def _mark_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> None:
        self._cleanup_inflight()
        key = self._commit_key(repo_name, branch, commit_sha)
        self._inflight_commits[key] = datetime.utcnow().timestamp()

    def _unmark_commit_inflight(self, repo_name: str, branch: str, commit_sha: str) -> None:
        key = self._commit_key(repo_name, branch, commit_sha)
        self._inflight_commits.pop(key, None)

    def _reserve_commit_processing(self, repo_name: str, branch: str, commit_sha: str) -> bool:
        normalized = self._normalize_repo_name(repo_name)
        if self._is_duplicate_push(normalized, branch, commit_sha):
            return False
        if self._is_commit_inflight(normalized, branch, commit_sha):
            return False
        self._mark_commit_inflight(normalized, branch, commit_sha)
        return True

    def _normalize_repo_name(self, repo_name: str) -> str:
        if not repo_name:
            return repo_name
        projects = self.config.projects if isinstance(self.config.projects, dict) else {}
        for key in projects.keys():
            if key.lower() == repo_name.lower():
                return key
        return repo_name.lower()

    def _run_git(self, repo_path: Path, args: list, timeout: int = 15) -> Optional[str]:
        try:
            result = subprocess.run(
                ['git'] + args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode != 0:
                self.logger.debug(f"Git command failed: {' '.join(args)}: {result.stderr.strip()}")
                return None
            return result.stdout.strip()
        except Exception as e:
            self.logger.debug(f"Git command error: {' '.join(args)}: {e}")
            return None

    def _safe_git_fetch(self, repo_path: Path) -> None:
        result = self._run_git(repo_path, ['fetch', '--all', '--prune'], timeout=60)
        if result is None:
            self.logger.debug(f"Git fetch failed for {repo_path}")

    def _get_repo_branch(self, repo_path: Path, project_config: Dict) -> str:
        branch = self._run_git(repo_path, ['rev-parse', '--abbrev-ref', 'HEAD'])
        if branch and branch != 'HEAD':
            return branch
        deploy_branch = project_config.get('deploy', {}).get('branch')
        return deploy_branch or 'main'

    def _get_upstream_ref(self, repo_path: Path) -> Optional[str]:
        return self._run_git(repo_path, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])

    def _get_commit_sha(self, repo_path: Path, ref: str) -> Optional[str]:
        return self._run_git(repo_path, ['rev-parse', ref])

    def _normalize_repo_url(self, raw_url: Optional[str]) -> Optional[str]:
        if not raw_url:
            return None
        url = raw_url.strip()

        if url.startswith('git@'):
            # git@github.com:owner/repo.git
            remainder = url.split('@', 1)[1]
            if ':' in remainder:
                host, path = remainder.split(':', 1)
                url = f"https://{host}/{path}"
        elif url.startswith('ssh://git@'):
            url = url.replace('ssh://git@', 'https://')
        elif url.startswith('git://'):
            url = url.replace('git://', 'https://')

        if url.endswith('.git'):
            url = url[:-4]
        return url

    def _get_repo_url(self, repo_path: Path) -> Optional[str]:
        raw_url = self._run_git(repo_path, ['config', '--get', 'remote.origin.url'])
        return self._normalize_repo_url(raw_url)

    def _git_log_commits(self, repo_path: Path, rev_spec: str,
                         repo_url: Optional[str], max_commits: Optional[int]) -> list:
        if not rev_spec:
            return []

        format_str = "%H%x1f%an%x1f%B%x1e"
        cmd = ['log', '--no-color', f'--pretty=format:{format_str}']
        if max_commits:
            cmd.insert(1, f'-n{max_commits}')
        cmd.append(rev_spec)

        output = self._run_git(repo_path, cmd, timeout=30)
        if not output:
            return []

        commits = []
        entries = output.strip("\n\x1e").split("\x1e")
        for entry in entries:
            if not entry.strip():
                continue
            parts = entry.split("\x1f")
            if len(parts) < 3:
                continue
            commit_sha = parts[0].strip()
            author = parts[1].strip()
            message = parts[2].strip()
            commit_url = f"{repo_url}/commit/{commit_sha}" if repo_url else ""
            commits.append({
                'id': commit_sha,
                'author': {'name': author},
                'message': message or '(no message)',
                'url': commit_url
            })

        commits.reverse()
        return commits

    def _get_commits_between(self, repo_path: Path, start_sha: Optional[str],
                             end_ref: str, repo_url: Optional[str]) -> list:
        commits = []
        if start_sha:
            commits = self._git_log_commits(
                repo_path,
                f"{start_sha}..{end_ref}",
                repo_url,
                self.local_polling_max_commits
            )
        if commits:
            return commits

        fallback_limit = min(self.local_polling_max_commits, 5)
        return self._git_log_commits(
            repo_path,
            end_ref,
            repo_url,
            fallback_limit
        )

    def _split_embed_description(self, description: str, max_length: int = 4096) -> list[str]:
        """
        Split a long description into multiple chunks that fit Discord's limits.

        Args:
            description: The full description text
            max_length: Maximum length per chunk (Discord limit: 4096)

        Returns:
            List of description chunks
        """
        if len(description) <= max_length:
            return [description]

        chunks = []
        current_chunk = ""

        # Split by paragraphs first (double newline)
        paragraphs = description.split('\n\n')

        for paragraph in paragraphs:
            # If adding this paragraph would exceed limit, save current chunk
            if len(current_chunk) + len(paragraph) + 2 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""

                # If single paragraph is too long, split by lines
                if len(paragraph) > max_length:
                    lines = paragraph.split('\n')
                    for line in lines:
                        if len(current_chunk) + len(line) + 1 > max_length:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = line + "\n"
                        else:
                            current_chunk += line + "\n"
                else:
                    current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"

        # Add remaining chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _format_user_friendly_commit(self, message: str) -> str:
        """Convert technical commit message to user-friendly text."""
        # Remove conventional commit prefixes
        message = message.replace('feat:', '').replace('fix:', '').replace('chore:', '')
        message = message.replace('docs:', '').replace('style:', '').replace('refactor:', '')
        message = message.replace('perf:', '').replace('test:', '').replace('build:', '')
        message = message.replace('ci:', '').replace('improve:', '').replace('update:', '')

        # Remove issue references for cleaner look (keep in internal)
        import re
        message = re.sub(r'\(#\d+\)', '', message)
        message = re.sub(r'#\d+', '', message)
        message = re.sub(r'Fixes? #\d+', '', message, flags=re.IGNORECASE)
        message = re.sub(r'Closes? #\d+', '', message, flags=re.IGNORECASE)

        # Clean up whitespace
        message = ' '.join(message.split())
        message = message.strip().strip(':').strip()

        # Capitalize first letter
        if message:
            message = message[0].upper() + message[1:]

        return message

    def _build_code_changes_context(self, commits: list, project_path: Optional[Path]) -> str:
        """Build a truncated diff summary for a handful of commits."""
        if not self.patch_notes_include_diffs or not commits or not project_path:
            return ""

        try:
            repo_path = Path(project_path)
        except Exception:
            return ""

        analyzer = GitHistoryAnalyzer(str(repo_path))
        if not analyzer.is_git_repository():
            return ""

        max_commits = min(self.patch_notes_diff_max_commits, len(commits))
        if max_commits <= 0:
            return ""

        sections = []
        for commit in commits[-max_commits:]:
            commit_id = commit.get('id') or commit.get('sha') or commit.get('hash')
            if not commit_id:
                continue
            diff = analyzer.get_code_changes_for_commit(commit_id, self.patch_notes_diff_max_lines)
            if not diff:
                continue
            title = commit.get('message', '').split('\n')[0].strip()
            short_id = commit_id[:7]
            label = f"{short_id} {title}".strip()
            sections.append(f"## {label}\n{diff}")

        if not sections:
            return ""

        return "CODE CHANGES (DIFF SUMMARY, MAY BE TRUNCATED):\n\n" + "\n\n".join(sections)

    def _load_patch_notes_context(self, project_config: Optional[Dict],
                                  project_path: Optional[Path]) -> str:
        """Load optional context files for richer patch notes prompts."""
        if not project_config:
            return ""

        patch_config = project_config.get('patch_notes', {})
        context_files = patch_config.get('context_files') or patch_config.get('context_file')
        if not context_files:
            return ""

        if isinstance(context_files, str):
            context_files = [context_files]
        if not isinstance(context_files, list):
            return ""

        base_path = project_path
        if not base_path:
            base = project_config.get('path', '')
            base_path = Path(base) if base else None

        per_file_limit = int(patch_config.get('context_max_chars', 1500))
        total_limit = int(patch_config.get('context_total_max_chars', 4000))

        sections = []
        total_chars = 0

        for entry in context_files:
            if not entry:
                continue
            entry_path = Path(entry)
            if not entry_path.is_absolute() and base_path:
                entry_path = base_path / entry_path
            if not entry_path.exists():
                continue
            try:
                content = entry_path.read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                continue

            if not content:
                continue

            if per_file_limit > 0 and len(content) > per_file_limit:
                head_len = max(1, per_file_limit // 2)
                tail_len = per_file_limit - head_len
                content = (
                    content[:head_len].rstrip()
                    + "\n... (snip) ...\n"
                    + content[-tail_len:].lstrip()
                )

            section = f"PROJECT CONTEXT FILE: {entry_path.name}\n{content}"
            total_chars += len(section)
            if total_limit > 0 and total_chars > total_limit:
                break
            sections.append(section)

        if not sections:
            return ""

        return "PROJECT CONTEXT (REFERENCE):\n\n" + "\n\n".join(sections)

    def _build_changelog_fallback_description(self, project_config: Optional[Dict], language: str) -> str:
        """Build a user-facing description from CHANGELOG.md if present."""
        if not project_config:
            return ""

        project_path = project_config.get('path')
        if not project_path:
            return ""

        changelog_path = Path(project_path) / 'CHANGELOG.md'
        if not changelog_path.exists():
            return ""

        try:
            from utils.changelog_parser import get_changelog_parser
            parser = get_changelog_parser(Path(project_path))
            version = parser.get_latest_version()
            if not version:
                return ""
            version_data = parser.get_version_section(version)
            if not version_data:
                return ""

            header = f"**Version {version}**"
            if version_data.get('title'):
                header = f"{header} — {version_data['title']}"

            content = version_data.get('content', '').strip()
            if not content:
                return ""

            # Keep changelog content as the primary source (already structured).
            if language == 'de':
                return f"{header}\n\n{content}"
            return f"{header}\n\n{content}"
        except Exception as e:
            self.logger.warning(f"⚠️ CHANGELOG Fallback failed: {e}")
            return ""

    def _is_patch_notes_too_short(self, response: str, commits: list) -> bool:
        """Heuristic to detect underspecified AI output."""
        if not response:
            return True

        bullet_count = sum(
            1 for line in response.splitlines()
            if line.strip().startswith('•')
        )
        commit_detail = any(
            len([l for l in (c.get('message') or '').splitlines() if l.strip()]) >= 5
            for c in commits
        )

        if len(commits) > 1 or commit_detail:
            min_bullets = 3
        else:
            min_bullets = 1

        if bullet_count < min_bullets:
            return True

        if commit_detail and len(response) < 300:
            return True

        return False

    async def _generate_ai_patch_notes(self, commits: list, language: str, repo_name: str,
                                       project_config: Optional[Dict] = None) -> Optional[str]:
        """
        Generate professional, user-friendly patch notes using AI with training system.

        NEW: Uses CHANGELOG.md + AI Training System for better quality!

        Process:
        1. Load CHANGELOG.md if available (fullest information source)
        2. Use PatchNotesTrainer to build enhanced prompt with examples
        3. Generate patch notes with AI
        4. Calculate quality score
        5. Save high-quality examples for future training

        Args:
            commits: List of commit dictionaries
            language: 'de' or 'en'
            repo_name: Repository name for context
            project_config: Optional project configuration

        Returns:
            AI-generated patch notes string or None if failed
        """
        if not self.ai_service or not commits:
            return None

        # Try to get CHANGELOG content
        changelog_content = ""
        version = None
        version_data = None

        project_path = None
        if project_config:
            project_path = Path(project_config.get('path', ''))
            changelog_path = project_path / 'CHANGELOG.md'

            if changelog_path.exists():
                try:
                    # Detect version from commits
                    import re
                    for commit in commits:
                        msg = commit.get('message', '')
                        match = re.search(r'v?(?:ersion|elease)?\s*([0-9]+\.[0-9]+\.[0-9]+)', msg, re.IGNORECASE)
                        if match:
                            version = match.group(1)
                            break

                    from utils.changelog_parser import get_changelog_parser
                    parser = get_changelog_parser(project_path)

                    # Get CHANGELOG section if version found
                    if version:
                        version_data = parser.get_version_section(version)

                        if version_data:
                            changelog_content = version_data['content']
                            self.logger.info(f"📖 Using CHANGELOG.md section for v{version} ({len(changelog_content)} chars)")
                        else:
                            self.logger.info(f"⚠️ Version {version} not found in CHANGELOG, using commits only")
                    else:
                        # Fallback: use latest version from CHANGELOG
                        latest = parser.get_latest_version()
                        if latest:
                            version = latest
                            version_data = parser.get_version_section(version)
                            if version_data:
                                changelog_content = version_data['content']
                                self.logger.info(
                                    f"📖 Using latest CHANGELOG.md section for v{version} "
                                    f"({len(changelog_content)} chars)"
                                )
                        if not changelog_content:
                            self.logger.info("⚠️ No version detected in commits, using commits only")

                except Exception as e:
                    self.logger.warning(f"⚠️ Could not parse CHANGELOG: {e}")

        project_context = self._load_patch_notes_context(project_config, project_path)

        # Build enhanced prompt with A/B Testing
        selected_variant = None
        variant_id = None

        code_changes_context = self._build_code_changes_context(commits, project_path)

        if self.patch_notes_trainer and self.prompt_ab_testing and (changelog_content or project_config):
            try:
                # Select prompt variant using A/B testing (weighted by performance)
                selected_variant = self.prompt_ab_testing.select_variant(
                    project=repo_name,
                    strategy='weighted_random'
                )
                variant_id = selected_variant.id

                self.logger.info(f"🧪 A/B Test: Using variant '{selected_variant.name}' (ID: {variant_id}) with language '{language}'")

                # Build prompt from variant template (language-specific)
                variant_template = self.prompt_ab_testing.get_variant_template(
                    variant_id=variant_id,
                    language=language
                )
                prompt = variant_template.format(
                    project=repo_name,
                    changelog=changelog_content or "No CHANGELOG available",
                    commits='\n'.join([f"- {c.get('message', '')}" for c in commits[:10]])
                )

                # Add examples from trainer
                if self.patch_notes_trainer.good_examples:
                    prompt += "\n\n# EXAMPLES OF HIGH-QUALITY PATCH NOTES\n\n"
                    for i, example in enumerate(self.patch_notes_trainer.good_examples[:2], 1):
                        prompt += f"## Example {i} ({example['project']} v{example['version']}):\n"
                        prompt += f"```\n{example['generated_notes'][:400]}...\n```\n\n"

                if code_changes_context:
                    prompt += f"\n\n{code_changes_context}"
                if project_context:
                    prompt += f"\n\n{project_context}"

            except Exception as e:
                self.logger.warning(f"⚠️ A/B Testing failed, using enhanced prompt: {e}")
                try:
                    prompt = self.patch_notes_trainer.build_enhanced_prompt(
                        changelog_content=changelog_content,
                        commits=commits,
                        language=language,
                        project=repo_name
                    )
                    if code_changes_context:
                        prompt += f"\n\n{code_changes_context}"
                    if project_context:
                        prompt += f"\n\n{project_context}"
                    self.logger.info(f"🎯 Using enhanced AI prompt with training examples")
                except Exception as e2:
                    self.logger.warning(f"⚠️ Enhanced prompt failed, using fallback: {e2}")
                    prompt = self._build_fallback_prompt(
                        commits,
                        language,
                        repo_name,
                        changelog_content,
                        code_changes_context,
                        project_context
                    )
        else:
            # Fallback to original prompt if no trainer available
            prompt = self._build_fallback_prompt(
                commits,
                language,
                repo_name,
                changelog_content,
                code_changes_context,
                project_context
            )

        # Log commits being processed
        num_commits = len(commits)
        self.logger.info(f"🔍 AI Processing {num_commits} commit(s) for {repo_name}:")
        for i, commit in enumerate(commits[:5], 1):
            msg = commit.get('message', '').split('\n')[0]
            self.logger.info(f"   {i}. {msg}")
        if num_commits > 5:
            self.logger.info(f"   ... and {num_commits - 5} more commits")

        # Call AI Service
        patch_config = project_config.get('patch_notes', {}) if project_config else {}
        use_critical_model = patch_config.get('use_critical_model', True)
        try:
            ai_response = await self.ai_service.get_raw_ai_response(
                prompt=prompt,
                use_critical_model=use_critical_model
            )

            if not ai_response:
                return None

            # Clean up response
            response = ai_response.strip()

            # Ensure it starts with a category
            if not response.startswith('**'):
                lines = response.split('\n')
                start_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('**'):
                        start_idx = i
                        break
                response = '\n'.join(lines[start_idx:])

            if self._is_patch_notes_too_short(response, commits):
                self.logger.info("⚠️ AI Patch Notes zu kurz, starte zweiten Durchlauf mit striktem Prompt")
                strict_prompt = self._build_fallback_prompt(
                    commits,
                    language,
                    repo_name,
                    changelog_content,
                    code_changes_context,
                    project_context,
                    strict=True
                )
                retry = await self.ai_service.get_raw_ai_response(
                    prompt=strict_prompt,
                    use_critical_model=True
                )
                if retry:
                    retry = retry.strip()
                    if not retry.startswith('**'):
                        retry_lines = retry.split('\n')
                        retry_start = 0
                        for i, line in enumerate(retry_lines):
                            if line.startswith('**'):
                                retry_start = i
                                break
                        retry = '\n'.join(retry_lines[retry_start:])
                    if retry and len(retry) >= len(response):
                        response = retry

            # Calculate quality score and record A/B test result
            quality_context = changelog_content or project_context
            if self.patch_notes_trainer and quality_context:
                try:
                    if not version and commits:
                        version = (commits[-1].get('id') or commits[-1].get('sha') or '')[:7] or None
                    quality_score = self.patch_notes_trainer.calculate_quality_score(
                        generated_notes=response,
                        changelog_content=quality_context
                    )
                    self.logger.info(f"📊 Patch Notes Quality Score: {quality_score:.1f}/100")

                    # Record A/B test result if variant was used
                    if self.prompt_ab_testing and variant_id:
                        self.prompt_ab_testing.record_result(
                            variant_id=variant_id,
                            project=repo_name,
                            version=version,
                            quality_score=quality_score,
                            user_feedback_score=0.0  # Will be updated from reactions
                        )
                        self.logger.info(f"🧪 A/B Test result recorded for variant {variant_id}")

                        # Schedule auto-tuning check (runs if conditions met)
                        if self.prompt_auto_tuner:
                            try:
                                self.prompt_auto_tuner.schedule_auto_tuning(
                                    project=repo_name,
                                    min_samples=10,
                                    improvement_threshold=5.0
                                )
                            except Exception as e:
                                self.logger.debug(f"Auto-tuning check skipped: {e}")

                    # Save as training example when a version is available
                    if version:
                        self.patch_notes_trainer.save_example(
                            version=version,
                            changelog_content=quality_context,
                            generated_notes=response,
                            quality_score=quality_score,
                            project=repo_name
                        )

                    if quality_score >= 80:
                        self.logger.info(f"🌟 High-quality patch notes! Saved as training example.")
                except Exception as e:
                    self.logger.warning(f"⚠️ Quality scoring failed: {e}")

            self.logger.info(f"✅ AI generated patch notes for {repo_name} ({len(response)} chars)")
            return response if response else None

        except Exception as e:
            self.logger.error(f"AI patch notes generation failed: {e}")
            return None

    def _build_fallback_prompt(self, commits: list, language: str, repo_name: str,
                               changelog_content: str = "", code_changes_context: str = "",
                               project_context: str = "", strict: bool = False) -> str:
        """Build fallback prompt when trainer is not available."""
        # Build commit summary for AI
        commit_summaries = []
        for commit in commits:
            full_msg = commit.get('message', '')
            lines = full_msg.split('\n')
            title = lines[0]

            # Get body (skip empty lines after title)
            body_lines = []
            for line in lines[1:]:
                if line.strip():
                    body_lines.append(line)

            author = commit.get('author', {}).get('name', 'Unknown')

            # Include full message if it has substantial body
            if len(body_lines) > 2:
                body = '\n'.join(body_lines[:30])
                commit_summaries.append(f"- {title}\n  {body}\n  (by {author})")
            else:
                commit_summaries.append(f"- {title} (by {author})")

        commits_text = "\n".join(commit_summaries)
        num_commits = len(commits)
        detail_instruction = ""

        extra_sections = []
        if changelog_content:
            extra_sections.append(f"CHANGELOG INFORMATION:\n{changelog_content}")
        if code_changes_context:
            extra_sections.append(code_changes_context)
        if project_context:
            extra_sections.append(project_context)
        extra_context = "\n\n".join(extra_sections).strip()
        if extra_context:
            extra_context = f"\n\n{extra_context}\n"

        if num_commits > 30:
            # Many commits - ask for high-level overview
            if language == 'de':
                detail_instruction = f"\n\n⚠️ WICHTIG: Es gibt {num_commits} Commits! Erstelle eine HIGH-LEVEL Übersicht. Gruppiere ähnliche Commits und beschreibe große Features detailliert, aber fasse Kleinigkeiten zusammen."
            else:
                detail_instruction = f"\n\n⚠️ IMPORTANT: There are {num_commits} commits! Create a HIGH-LEVEL overview. Group similar commits and describe major features in detail, but summarize minor changes."
        elif num_commits > 15:
            # Medium amount - balanced approach, but RECOGNIZE major features
            if language == 'de':
                detail_instruction = f"\n\n⚠️ Es gibt {num_commits} Commits. Gruppiere verwandte Commits (z.B. alle zum gleichen Feature-Namen) zu EINEM detaillierten Feature-Punkt. Release-Features sind GROSS und benötigen detaillierte Erklärung!"
            else:
                detail_instruction = f"\n\n⚠️ There are {num_commits} commits. Group related commits (e.g., all commits for the same feature) into ONE detailed feature point. Release features are MAJOR and need detailed explanation!"

        # Build prompt based on language
        strict_rules = ""
        if strict:
            min_bullets = max(3, len(commits))
            min_chars = 400 if len(commits) > 1 else 250
            if language == 'de':
                strict_rules = (
                    "\n\nSTRICTE QUALITAETSREGELN:\n"
                    f"- Nutze mindestens {min_bullets} Bulletpoints (bei vorhandenen Detail-Infos).\n"
                    f"- Ziel: mindestens {min_chars} Zeichen, wenn Commit-Body oder Diff Details enthalten.\n"
                    "- Erzeuge KEINE Einzeiler-Ausgabe.\n"
                )
            else:
                strict_rules = (
                    "\n\nSTRICT QUALITY RULES:\n"
                    f"- Use at least {min_bullets} bullet points when detailed info exists.\n"
                    f"- Target at least {min_chars} characters if commit body or diff includes details.\n"
                    "- Do NOT return a single-line answer.\n"
                )

        if language == 'de':
            prompt = f"""Du bist ein professioneller Technical Writer. Erstelle benutzerfreundliche Patch Notes für das Projekt "{repo_name}".

COMMITS (VOLLSTÄNDIGE LISTE):
{commits_text}
{extra_context}

KRITISCHE REGELN:
⚠️ BESCHREIBE NUR ÄNDERUNGEN DIE WIRKLICH IN DEN COMMITS OBEN STEHEN!
⚠️ ERFINDE KEINE FEATURES ODER FIXES DIE NICHT IN DER COMMIT-LISTE SIND!
⚠️ Wenn ein Commit unklar ist, überspringe ihn lieber als zu raten!
⚠️ Nutze CHANGELOG INFORMATION und CODE CHANGES (falls vorhanden) für Details.
⚠️ Wenn Texte "offen", "todo", "still open" oder "risiken" nennen, markiere sie NICHT als abgeschlossen.

WICHTIG - ZUSAMMENHÄNGENDE FEATURES ERKENNEN:
🔍 Suche nach VERWANDTEN Commits die zusammengehören (z.B. mehrere "fix:" oder "feat:" Commits für das gleiche Feature)
🔍 Release-Commits (feat: Release v...) enthalten oft GROSSE Änderungen - beschreibe diese DETAILLIERT!
🔍 Commit-Serien mit gleichem Feature-Namen sind EINZELNE Features, nicht getrennte Punkte!
🔍 Bei großen Refactorings: Erkenne die GESAMTBEDEUTUNG, nicht nur Einzelschritte!
🔍 Wenn Commit-Bodies Abschnitte enthalten (z.B. "Rate Limiting:", "Monitoring:"), nutze pro Abschnitt einen Bulletpoint.
🔍 Bei reinen Doku-/Status-Updates: als Status-Update zusammenfassen, keine Features erfinden.

AUFGABE:
Fasse diese Commits zu professionellen, DETAILLIERTEN Patch Notes zusammen:{detail_instruction}{strict_rules}

1. GRUPPIERE verwandte Commits zu EINEM ausführlichen Bulletpoint
2. Kategorisiere in: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
3. Verwende einfache, klare Sprache aber sei AUSFÜHRLICH
4. Beginne mit Nutzer-Nutzen, danach technische Details
5. Bei großen Features: 3-5 Sätze oder Sub-Bulletpoints mit Details
6. Entferne Jargon und technische Präfixe
7. Zielgruppe: Endkunden die verstehen wollen was sich verbessert hat
8. Maximal 8000 Zeichen - nutze den Platz aus!
9. Erfinde keine Details und wiederhole keine Beispiel-Formulierungen.
9. Erfinde keine Details und verwende keine Beispiel-Formulierungen aus dem Prompt.

FORMAT:
Verwende Markdown mit ** für Kategorien und • für Hauptpunkte.
Bei komplexen Features: Nutze Sub-Bulletpoints (Einrückung mit 2 Leerzeichen).

FORMAT-BEISPIEL:
**🆕 Neue Features:**
• **Feature-Name**: Detaillierte Beschreibung was das Feature macht und warum es wichtig ist.
  - Erster Nutzen oder technisches Detail
  - Zweiter Nutzen oder technisches Detail
  - Dritter Nutzen oder technisches Detail

**🐛 Bugfixes:**
• **Bug-Kategorie**: Was wurde gefixt und welches Problem hatte es verursacht

**⚡ Verbesserungen:**
• **Verbesserung**: Detaillierte Beschreibung der Verbesserung

Erstelle JETZT die DETAILLIERTEN Patch Notes basierend auf den ECHTEN Commits oben (nur die Kategorien + Bulletpoints, keine Einleitung):"""
        else:  # English
            prompt = f"""You are a professional Technical Writer. Create user-friendly patch notes for the project "{repo_name}".

COMMITS (COMPLETE LIST):
{commits_text}
{extra_context}

CRITICAL RULES:
⚠️ ONLY DESCRIBE CHANGES THAT ARE ACTUALLY IN THE COMMITS ABOVE!
⚠️ NEVER INVENT FEATURES OR FIXES THAT ARE NOT IN THE COMMIT LIST!
⚠️ If a commit is unclear, skip it rather than guessing!
⚠️ Use CHANGELOG INFORMATION and CODE CHANGES (if present) for details.
⚠️ If text says "open", "todo", "still open", or "risks", do NOT mark it as completed.

IMPORTANT - RECOGNIZE RELATED FEATURES:
🔍 Look for RELATED commits that belong together (e.g., multiple "fix:" or "feat:" commits for the same feature)
🔍 Release commits (feat: Release v...) often contain MAJOR changes - describe these in DETAIL!
🔍 Commit series with the same feature name are SINGLE features, not separate items!
🔍 For large refactorings: Recognize the OVERALL SIGNIFICANCE, not just individual steps!
🔍 If commit bodies include sections (e.g., "Rate Limiting:"), use one bullet per section.
🔍 For doc/status-only updates: summarize as status updates; do not invent features.

TASK:
Summarize these commits into professional, DETAILED patch notes:{detail_instruction}{strict_rules}

1. GROUP related commits into ONE comprehensive bulletpoint
2. Categorize into: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
3. Use simple, clear language but be COMPREHENSIVE
4. Lead with user impact, then technical details
5. For major features: 3-5 sentences or sub-bulletpoints with details
6. Remove jargon and technical prefixes
7. Target audience: End customers who want to understand what improved
8. Maximum 8000 characters - use the space!
9. Do not invent details or reuse example wording.
9. Do not invent details and do not reuse example wording from the prompt.

FORMAT:
Use Markdown with ** for categories and • for main points.
For complex features: Use sub-bulletpoints (indented with 2 spaces).

FORMAT EXAMPLE:
**🆕 New Features:**
• **Feature Name**: Detailed description of what the feature does and why it's important.
  - First benefit or technical detail
  - Second benefit or technical detail
  - Third benefit or technical detail

**🐛 Bug Fixes:**
• **Bug Category**: What was fixed and what problem it caused

**⚡ Improvements:**
• **Improvement**: Detailed description of the improvement

Create the DETAILED patch notes NOW based on the REAL commits above (only categories + bulletpoints, no introduction):"""

        return prompt

    async def _send_pr_notification(
        self, action: str, repo: str, pr_number: int, title: str,
        author: str, source: str, target: str, url: str
    ):
        """Send Discord notification for PR event"""
        channel_id = self.code_fixes_channel_id or self.deployment_channel_id
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        action_emojis = {
            'opened': '🔓',
            'closed': '🔒',
            'reopened': '🔄',
            'synchronize': '🔃',
            'merged': '🎉'
        }

        emoji = action_emojis.get(action, '🔀')
        color = discord.Color.green() if action in ['opened', 'merged'] else discord.Color.orange()

        embed = discord.Embed(
            title=f"{emoji} Pull Request #{pr_number} {action}",
            description=f"**{title}**",
            url=url,
            color=color,
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Branch", value=f"`{source}` → `{target}`", inline=False)

        await channel.send(embed=embed)

    async def _send_release_notification(
        self, action: str, repo: str, tag: str, name: str,
        author: str, is_prerelease: bool, url: str
    ):
        """Send Discord notification for release event"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        emoji = '🏷️' if is_prerelease else '🎉'
        release_type = 'Pre-release' if is_prerelease else 'Release'

        embed = discord.Embed(
            title=f"{emoji} {release_type} {action}: {name}",
            description=f"**{repo}** `{tag}`",
            url=url,
            color=discord.Color.purple() if is_prerelease else discord.Color.gold(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Tag", value=f"`{tag}`", inline=True)
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Type", value=release_type, inline=True)

        await channel.send(embed=embed)

    async def _send_deployment_success(
        self, repo: str, branch: str, sha: str, result: Dict
    ):
        """Send Discord notification for successful deployment"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        duration = result.get('duration_seconds', 0)

        embed = discord.Embed(
            title="✅ Deployment Successful",
            description=f"**{repo}** deployed successfully",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)
        embed.add_field(name="Duration", value=f"{duration:.1f}s", inline=True)

        if result.get('tests_passed'):
            embed.add_field(name="Tests", value="✅ Passed", inline=True)

        await channel.send(embed=embed)

    async def _send_deployment_failure(
        self, repo: str, branch: str, sha: str, result: Dict
    ):
        """Send Discord notification for failed deployment"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        error = result.get('error', 'Unknown error')
        rollback = result.get('rolled_back', False)

        embed = discord.Embed(
            title="❌ Deployment Failed",
            description=f"**{repo}** deployment failed",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)

        # Truncate error if too long
        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Error", value=f"```{error}```", inline=False)

        if rollback:
            embed.add_field(name="Rollback", value="✅ Auto-rollback successful", inline=False)

        await channel.send(embed=embed)

    async def _send_deployment_error(
        self, repo: str, branch: str, sha: str, error: str
    ):
        """Send Discord notification for deployment exception"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title="💥 Deployment Exception",
            description=f"**{repo}** deployment crashed",
            color=discord.Color.dark_red(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Repository", value=repo, inline=True)
        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commit", value=f"`{sha}`", inline=True)

        # Truncate error if too long
        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Exception", value=f"```{error}```", inline=False)

        await channel.send(embed=embed)

    async def _send_external_git_notifications(self, repo_name: str, embed: discord.Embed,
                                                project_config: Dict, version: str = None):
        """
        Send Git push notifications to external servers (customer guilds)
        AND activate feedback collection for AI learning.

        Args:
            repo_name: Repository name
            embed: The embed to send (customer-friendly patch notes)
            project_config: Project configuration dictionary
            version: Version number (for feedback tracking)
        """
        # Get external notifications config
        external_notifs = project_config.get('external_notifications', [])
        if not external_notifs:
            return

        for notif_config in external_notifs:
            if not notif_config.get('enabled', False):
                continue

            # Check if git_push notifications are enabled
            notify_on = notif_config.get('notify_on', {})
            if not notify_on.get('git_push', True):
                continue

            # Get channel
            channel_id = notif_config.get('channel_id')
            if not channel_id:
                continue

            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    self.logger.warning(f"⚠️ External channel {channel_id} not found for {repo_name}")
                    continue

                # Check if description is too long and split if needed
                description_chunks = self._split_embed_description(embed.description or "")

                sent_message = None

                if len(description_chunks) <= 1:
                    # Single embed - send as is
                    sent_message = await channel.send(embed=embed)
                    self.logger.info(f"📤 Sent git update for {repo_name} to external server")
                else:
                    # Multiple embeds needed - split across messages
                    for i, chunk in enumerate(description_chunks):
                        embed_copy = discord.Embed(
                            title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                            url=embed.url,
                            color=embed.color,
                            description=chunk,
                            timestamp=embed.timestamp
                        )
                        if i == len(description_chunks) - 1:  # Add footer only to last embed
                            if embed.footer:
                                embed_copy.set_footer(text=embed.footer.text)

                        message = await channel.send(embed=embed_copy)

                        # Track the first message for feedback (main content)
                        if i == 0:
                            sent_message = message

                    self.logger.info(f"📤 Sent git update for {repo_name} to external server ({len(description_chunks)} parts)")

                # 🎯 ACTIVATE FEEDBACK COLLECTION for this message
                if sent_message and self.feedback_collector and version:
                    try:
                        await self.feedback_collector.track_patch_notes_message(
                            message=sent_message,
                            project=repo_name,
                            version=version
                        )
                        self.logger.info(f"👍 Feedback collection activated for {repo_name} v{version}")
                    except Exception as e:
                        self.logger.warning(f"⚠️ Could not activate feedback collection: {e}")

            except Exception as e:
                self.logger.error(f"❌ Failed to send external git notification for {repo_name}: {e}")
