"""
GitHub Integration for ShadowOps Bot
Handles webhook events, auto-deployment, and Discord notifications
"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Dict, Optional, Callable
from datetime import datetime
from aiohttp import web
import discord

logger = logging.getLogger(__name__)


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

        # GitHub webhook settings
        self.webhook_secret = config.get('github', {}).get('webhook_secret', '')
        self.webhook_port = config.get('github', {}).get('webhook_port', 8080)
        self.enabled = config.get('github', {}).get('enabled', False)

        # Deployment settings
        self.auto_deploy_enabled = config.get('github', {}).get('auto_deploy', False)
        self.deploy_branches = config.get('github', {}).get('deploy_branches', ['main', 'master'])

        # Discord notification channel
        self.deployment_channel_id = config.get('channels', {}).get('deployment_log', 0)

        # Webhook server
        self.app = None
        self.runner = None
        self.site = None

        # Event handlers registry
        self.event_handlers: Dict[str, Callable] = {
            'push': self.handle_push_event,
            'pull_request': self.handle_pr_event,
            'release': self.handle_release_event,
        }

        # Deployment manager (will be set by bot)
        self.deployment_manager = None

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

        self.site = web.TCPSite(self.runner, '0.0.0.0', self.webhook_port)
        await self.site.start()

        self.logger.info(f"🚀 GitHub webhook server started on port {self.webhook_port}")

    async def stop_webhook_server(self):
        """Stop the webhook HTTP server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self.logger.info("🛑 GitHub webhook server stopped")

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

    async def handle_push_event(self, payload: Dict):
        """
        Handle push events from GitHub

        Triggers auto-deployment if push is to a deployment branch
        """
        try:
            repo_name = payload['repository']['name']
            repo_full_name = payload['repository']['full_name']
            ref = payload['ref']  # refs/heads/main
            branch = ref.split('/')[-1]

            # Get commit info
            commits = payload.get('commits', [])
            commit_count = len(commits)

            if commits:
                latest_commit = commits[-1]
                commit_message = latest_commit['message']
                commit_author = latest_commit['author']['name']
                commit_sha = latest_commit['id'][:7]
            else:
                commit_message = "No commits"
                commit_author = "Unknown"
                commit_sha = "unknown"

            self.logger.info(
                f"📌 Push to {repo_name}/{branch}: "
                f"{commit_count} commit(s) by {commit_author}"
            )

            # Send Discord notification
            await self._send_push_notification(
                repo_full_name, branch, commit_count,
                commit_author, commit_message, commit_sha
            )

            # Auto-deploy if enabled and on deployment branch
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
        self, repo: str, branch: str, commit_count: int,
        author: str, message: str, sha: str
    ):
        """Send Discord notification for push event"""
        channel = self.bot.get_channel(self.deployment_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"📌 New Push to {repo}",
            description=f"**{commit_count}** commit(s) pushed to `{branch}`",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(name="Branch", value=f"`{branch}`", inline=True)
        embed.add_field(name="Commits", value=str(commit_count), inline=True)
        embed.add_field(name="Author", value=author, inline=True)

        # Truncate commit message if too long
        if len(message) > 200:
            message = message[:197] + "..."
        embed.add_field(name="Latest Commit", value=f"`{sha}` {message}", inline=False)

        await channel.send(embed=embed)

    async def _send_pr_notification(
        self, action: str, repo: str, pr_number: int, title: str,
        author: str, source: str, target: str, url: str
    ):
        """Send Discord notification for PR event"""
        channel = self.bot.get_channel(self.deployment_channel_id)
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
