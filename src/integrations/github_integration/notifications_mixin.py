"""
Discord notification methods for GitHubIntegration.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import discord

logger = logging.getLogger('shadowops')


class NotificationsMixin:

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
            timestamp=datetime.now(timezone.utc)
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
            timestamp=datetime.now(timezone.utc)
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

                    # ACTIVATE FEEDBACK COLLECTION for internal channel message
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
            timestamp=datetime.now(timezone.utc)
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
            timestamp=datetime.now(timezone.utc)
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
            timestamp=datetime.now(timezone.utc)
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
            timestamp=datetime.now(timezone.utc)
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
            timestamp=datetime.now(timezone.utc)
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

                # ACTIVATE FEEDBACK COLLECTION for this message
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
