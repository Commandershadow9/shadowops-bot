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

        # Discord notification channel
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

            self.logger.info(
                f"📌 Push to {repo_name}/{branch}: "
                f"{len(commits)} commit(s) by {pusher}"
            )

            # Send detailed patch notes notification
            await self._send_push_notification(
                repo_name=repo_name,
                repo_url=repo_url,
                branch=branch,
                pusher=pusher,
                commits=commits
            )

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
        # Find project config to get color and potential customer channel
        project_config = self.config.projects.get(repo_name, {})
        project_color = project_config.get('color', 0x3498DB) # Default blue

        # === INTERNAL EMBED (Technical, for developers) ===
        internal_embed = discord.Embed(
            title=f"🚀 Code Update: {repo_name}",
            url=f"{repo_url}/commits/{branch}",
            color=project_color,
            timestamp=datetime.utcnow()
        )
        internal_embed.set_author(name=pusher)

        commit_details = []
        for commit in commits:
            sha = commit['id'][:7]
            author = commit['author']['name']
            message = commit['message'].split('\n')[0] # First line of commit message
            url = commit['url']
            commit_details.append(f"[`{sha}`]({url}) {message} - *{author}*")

        if commit_details:
            internal_embed.description = "\n".join(commit_details)
        else:
            internal_embed.description = "No new commits in this push."

        # === CUSTOMER EMBED (User-friendly, categorized) ===
        customer_embed = discord.Embed(
            title=f"✨ Updates für {repo_name}",
            url=f"{repo_url}/commits/{branch}",
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
            description_parts.append("**🆕 Neue Features:**\n" + "\n".join(f"• {f}" for f in features))

        if fixes:
            description_parts.append("**🐛 Bugfixes:**\n" + "\n".join(f"• {f}" for f in fixes))

        if improvements:
            description_parts.append("**⚡ Verbesserungen:**\n" + "\n".join(f"• {i}" for i in improvements))

        if other:
            description_parts.append("**📝 Weitere Änderungen:**\n" + "\n".join(f"• {o}" for o in other))

        customer_embed.description = "\n\n".join(description_parts) if description_parts else "Diverse Updates und Verbesserungen"

        customer_embed.set_footer(text=f"{len(commits)} Commit(s) von {pusher}")

        # === AI-GENERATED PATCH NOTES (if enabled) ===
        patch_config = project_config.get('patch_notes', {})
        use_ai = patch_config.get('use_ai', False)
        language = patch_config.get('language', 'de')

        if use_ai and self.ai_service:
            try:
                self.logger.info(f"🤖 Generating AI patch notes for {repo_name} (language: {language})...")
                ai_description = await self._generate_ai_patch_notes(commits, language, repo_name)
                if ai_description:
                    customer_embed.description = ai_description
                    self.logger.info(f"✅ AI patch notes generated successfully")
            except Exception as e:
                self.logger.warning(f"⚠️ AI patch notes generation failed, using fallback: {e}")
                # Keep the categorized version as fallback

        # 1. Send to internal channel (technical embed)
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if internal_channel:
            try:
                await internal_channel.send(embed=internal_embed)
                self.logger.info(f"📢 Sent technical patch notes for {repo_name} to internal channel.")
            except Exception as e:
                self.logger.error(f"❌ Failed to send push notification to internal channel: {e}")

        # 2. Send to customer-facing channel (user-friendly embed)
        customer_channel_id = project_config.get('update_channel_id')
        if customer_channel_id:
            customer_channel = self.bot.get_channel(customer_channel_id)
            if customer_channel:
                try:
                    await customer_channel.send(embed=customer_embed)
                    self.logger.info(f"📢 Sent user-friendly patch notes for {repo_name} to customer channel {customer_channel_id}.")
                except Exception as e:
                    self.logger.error(f"❌ Failed to send push notification to customer channel {customer_channel_id}: {e}")
            else:
                self.logger.warning(f"⚠️ Customer update channel {customer_channel_id} for {repo_name} not found.")

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

    async def _generate_ai_patch_notes(self, commits: list, language: str, repo_name: str) -> Optional[str]:
        """
        Generate professional, user-friendly patch notes using AI (Ollama llama3.1)

        Args:
            commits: List of commit dictionaries
            language: 'de' or 'en'
            repo_name: Repository name for context

        Returns:
            AI-generated patch notes string or None if failed
        """
        if not self.ai_service or not commits:
            return None

        # Build commit summary for AI
        commit_summaries = []
        for commit in commits:
            msg = commit.get('message', '').split('\n')[0]  # First line
            author = commit.get('author', {}).get('name', 'Unknown')
            commit_summaries.append(f"- {msg} (by {author})")

        commits_text = "\n".join(commit_summaries)

        # Build prompt based on language
        if language == 'de':
            prompt = f"""Du bist ein professioneller Technical Writer. Erstelle benutzerfreundliche Patch Notes für das Projekt "{repo_name}".

COMMITS:
{commits_text}

AUFGABE:
Fasse diese Commits zu professionellen, verständlichen Patch Notes zusammen:

1. Kategorisiere in: 🆕 Neue Features, 🐛 Bugfixes, ⚡ Verbesserungen
2. Verwende einfache, klare Sprache (nicht-technisch)
3. Fokussiere auf NUTZEN für den User, nicht auf technische Details
4. Entferne Jargon, Issue-Nummern, und technische Präfixe
5. Schreibe zusammenhängend, nicht als rohe Liste
6. Maximal 3-4 Sätze pro Kategorie

FORMAT:
Verwende Markdown mit ** für Kategorien und • für Bulletpoints.
Keine Code-Blöcke, keine SHA Hashes, keine URLs.

BEISPIEL:
**🆕 Neue Features:**
• Dark Mode wurde hinzugefügt für bessere Nutzung bei Nacht
• Nutzerprofile zeigen jetzt Aktivitätsstatistiken

**🐛 Bugfixes:**
• Login-Probleme auf mobilen Geräten wurden behoben
• Datenbank-Timeouts treten nicht mehr auf

**⚡ Verbesserungen:**
• Ladezeiten wurden um 40% reduziert
• Die Benutzeroberfläche reagiert jetzt schneller

Erstelle JETZT die Patch Notes (nur die Kategorien + Bulletpoints, keine Einleitung):"""
        else:  # English
            prompt = f"""You are a professional Technical Writer. Create user-friendly patch notes for the project "{repo_name}".

COMMITS:
{commits_text}

TASK:
Summarize these commits into professional, accessible patch notes:

1. Categorize into: 🆕 New Features, 🐛 Bug Fixes, ⚡ Improvements
2. Use simple, clear language (non-technical)
3. Focus on USER BENEFIT, not technical details
4. Remove jargon, issue numbers, and technical prefixes
5. Write cohesively, not as raw list
6. Maximum 3-4 sentences per category

FORMAT:
Use Markdown with ** for categories and • for bulletpoints.
No code blocks, no SHA hashes, no URLs.

EXAMPLE:
**🆕 New Features:**
• Dark mode added for better night-time usage
• User profiles now show activity statistics

**🐛 Bug Fixes:**
• Login issues on mobile devices resolved
• Database timeouts no longer occur

**⚡ Improvements:**
• Loading times reduced by 40%
• Interface now responds faster

Create the patch notes NOW (only categories + bulletpoints, no introduction):"""

        # Call AI Service (uses llama3.1 for critical/important tasks)
        try:
            ai_response = await self.ai_service.generate_raw_ai_response(
                prompt=prompt,
                use_critical_model=True  # Use llama3.1 for best quality
            )

            if ai_response:
                # Clean up response (remove any extra text AI might add)
                response = ai_response.strip()

                # Ensure it starts with a category
                if not response.startswith('**'):
                    # Try to extract just the categorized part
                    lines = response.split('\n')
                    start_idx = 0
                    for i, line in enumerate(lines):
                        if line.startswith('**'):
                            start_idx = i
                            break
                    response = '\n'.join(lines[start_idx:])

                return response if response else None

        except Exception as e:
            self.logger.error(f"AI patch notes generation failed: {e}")
            return None

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
