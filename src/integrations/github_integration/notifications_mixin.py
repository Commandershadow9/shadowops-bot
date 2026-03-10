"""
Discord notification methods for GitHubIntegration.

v2: Dual-Format (Discord kurz + Web ausführlich), Batching, Stats, Feedback-Buttons
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

        project_color = project_config.get('color', 0x3498DB)  # Default blue
        patch_config = project_config.get('patch_notes', {})
        language = patch_config.get('language', 'de')

        # === BATCHING CHECK ===
        if hasattr(self, 'patch_notes_batcher') and self.patch_notes_batcher:
            if self.patch_notes_batcher.should_batch(commits, repo_name):
                result = self.patch_notes_batcher.add_commits(repo_name, commits)
                self.logger.info(
                    f"📦 Commits für {repo_name} gesammelt: "
                    f"{result['total_pending']} ausstehend (Ready: {result['ready']})"
                )

                if result['ready']:
                    # Batch ist voll — alle gesammelten Commits freigeben
                    all_commits = self.patch_notes_batcher.release_batch(repo_name)
                    if all_commits:
                        self.logger.info(f"🚀 Batch-Release: {len(all_commits)} gesammelte Commits")
                        commits = all_commits
                    # Weiter mit normaler Verarbeitung
                else:
                    # Noch nicht genug — nur interne Notification, keine Patch Notes
                    await self._send_internal_only(repo_name, repo_url, branch, pusher, commits, project_color)
                    return

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
            message = commit['message'].split('\n')[0]  # First line of commit message
            url = commit['url']
            if url:
                commit_details.append(f"[`{sha}`]({url}) {message} - *{author}*")
            else:
                commit_details.append(f"`{sha}` {message} - *{author}*")

        if commit_details:
            internal_embed.description = "\n".join(commit_details)
        else:
            internal_embed.description = "Keine neuen Commits in diesem Push."

        # === ADVANCED PATCH NOTES SYSTEM (if available) ===
        if self.patch_notes_manager and patch_config.get('use_advanced_system', False):
            try:
                self.logger.info(f"🎯 Using advanced patch notes system for {repo_name}")
                await self.patch_notes_manager.handle_git_push(
                    project_name=repo_name,
                    project_config=project_config,
                    commits=commits,
                    repo_name=repo_name
                )
                return
            except Exception as e:
                self.logger.warning(f"⚠️ Advanced system failed, falling back to legacy: {e}", exc_info=True)

        # === AI-GENERATED PATCH NOTES ===
        use_ai = patch_config.get('use_ai', False)

        ai_description = None
        if use_ai and self.ai_service:
            try:
                self.logger.info(f"🤖 Generiere KI Patch Notes für {repo_name} (Sprache: {language})...")
                ai_description = await self._generate_ai_patch_notes(commits, language, repo_name, project_config)
                if ai_description:
                    self.logger.info(f"✅ KI Patch Notes erfolgreich generiert")
            except Exception as e:
                self.logger.warning(f"⚠️ KI Patch Notes Generierung fehlgeschlagen, verwende Fallback: {e}")

        # === BUILD CUSTOMER EMBED (Dual-Format: Discord kurz) ===
        customer_embed = self._build_customer_embed(
            repo_name, commits_url, project_color, commits, language,
            ai_description, project_config
        )

        # === WEB EXPORT (SEO-optimiert, ausführlich) ===
        await self._export_web_changelog(repo_name, commits, ai_description, project_config, language)

        # 1. Send to internal channel (technical embed)
        await self._send_to_internal_channel(internal_embed, repo_name)

        # 2. Send to customer-facing channels with feedback collection
        version = self._extract_version_from_commits(commits)
        await self._send_to_customer_channels(customer_embed, repo_name, project_config, version)

        # 3. Send to external notification channels (customer servers) WITH feedback collection
        await self._send_external_git_notifications(repo_name, customer_embed, project_config, version)

    def _build_customer_embed(self, repo_name: str, commits_url: str,
                               project_color: int, commits: list, language: str,
                               ai_description: Optional[str],
                               project_config: Dict) -> discord.Embed:
        """Baue das Customer-Embed (Kurzformat für Discord)."""
        patch_config = project_config.get('patch_notes', {})

        # Language-specific texts
        if language == 'en':
            title_text = f"✨ Updates for {repo_name}"
        else:
            title_text = f"✨ Updates für {repo_name}"

        customer_embed = discord.Embed(
            title=title_text,
            url=commits_url,
            color=project_color,
            timestamp=datetime.now(timezone.utc)
        )

        if ai_description:
            customer_embed.description = ai_description
        else:
            # Fallback: Changelog oder Kategorisierung
            changelog_fallback = self._build_changelog_fallback_description(project_config, language)
            if changelog_fallback:
                customer_embed.description = changelog_fallback
            else:
                customer_embed.description = self._categorize_commits_text(commits, language)

        # Web-Link hinzufügen (falls konfiguriert)
        changelog_url = patch_config.get('changelog_url', '')
        if changelog_url:
            if language == 'de':
                customer_embed.add_field(
                    name="📖 Alle Details",
                    value=f"[Vollständige Patch Notes auf der Webseite]({changelog_url})",
                    inline=False
                )
            else:
                customer_embed.add_field(
                    name="📖 Full Details",
                    value=f"[Complete patch notes on the website]({changelog_url})",
                    inline=False
                )

        # Footer mit Stats
        footer_parts = [f"{len(commits)} Commit(s)"]

        # Git stats aus dem letzten AI-Aufruf lesen
        git_stats = getattr(self, '_last_git_stats', None)
        if git_stats:
            files = git_stats.get('files_changed', 0)
            if files > 0:
                footer_parts.append(f"{files} Dateien")
            added = git_stats.get('lines_added', 0)
            removed = git_stats.get('lines_removed', 0)
            if added > 0:
                footer_parts.append(f"+{added}/-{removed}")

        customer_embed.set_footer(text=" · ".join(footer_parts))

        return customer_embed

    def _categorize_commits_text(self, commits: list, language: str) -> str:
        """Kategorisiere Commits als Fallback-Text."""
        if language == 'en':
            feature_header = "**🆕 New Features:**"
            bugfix_header = "**🐛 Bug Fixes:**"
            improvement_header = "**⚡ Improvements:**"
            other_header = "**📝 Other Changes:**"
            default_desc = "Various updates and improvements"
        else:
            feature_header = "**🆕 Neue Features:**"
            bugfix_header = "**🐛 Bugfixes:**"
            improvement_header = "**⚡ Verbesserungen:**"
            other_header = "**📝 Weitere Änderungen:**"
            default_desc = "Diverse Updates und Verbesserungen"

        features = []
        fixes = []
        improvements = []
        other = []

        for commit in commits:
            message = commit['message'].split('\n')[0]
            message_lower = message.lower()

            if message_lower.startswith('feat') or 'feature' in message_lower or 'add' in message_lower:
                features.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('fix') or 'bug' in message_lower or 'issue' in message_lower:
                fixes.append(self._format_user_friendly_commit(message))
            elif message_lower.startswith('improve') or 'optimize' in message_lower or 'enhance' in message_lower or 'update' in message_lower:
                improvements.append(self._format_user_friendly_commit(message))
            else:
                other.append(self._format_user_friendly_commit(message))

        description_parts = []

        if features:
            description_parts.append(feature_header + "\n" + "\n".join(f"• {f}" for f in features))
        if fixes:
            description_parts.append(bugfix_header + "\n" + "\n".join(f"• {f}" for f in fixes))
        if improvements:
            description_parts.append(improvement_header + "\n" + "\n".join(f"• {i}" for i in improvements))
        if other:
            description_parts.append(other_header + "\n" + "\n".join(f"• {o}" for o in other))

        return "\n\n".join(description_parts) if description_parts else default_desc

    def _extract_version_from_commits(self, commits: list) -> Optional[str]:
        """Extrahiere Version aus Commit-Messages."""
        for commit in commits:
            msg = commit.get('message', '')
            match = re.search(r'v?(?:ersion|elease)?\s*([0-9]+\.[0-9]+\.[0-9]+)', msg, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def _export_web_changelog(self, repo_name: str, commits: list,
                                     ai_description: Optional[str],
                                     project_config: Dict, language: str) -> None:
        """Exportiere Patch Notes als Web-Changelog (SEO-optimiert)."""
        web_exporter = getattr(self, 'web_exporter', None)
        if not web_exporter:
            return

        version = self._extract_version_from_commits(commits)
        if not version:
            return

        git_stats = getattr(self, '_last_git_stats', None) or {}

        # TL;DR aus AI-Description extrahieren
        tldr = ""
        content = ai_description or ""
        if content:
            # TL;DR aus dem Text extrahieren (nach "TL;DR:" suchen)
            tldr_match = re.search(r'\*\*TL;DR:\*\*\s*(.+?)(?:\n|$)', content)
            if tldr_match:
                tldr = tldr_match.group(1).strip()
            else:
                # Ersten Satz als TL;DR
                first_line = content.split('\n')[0].strip()
                if first_line and not first_line.startswith('**'):
                    tldr = first_line
                else:
                    tldr = f"{repo_name} {version} Update"

        title = f"{repo_name} {version}"

        try:
            web_exporter.export(
                project=repo_name,
                version=version,
                title=title,
                tldr=tldr,
                content=content,
                stats=git_stats,
                language=language,
            )
            self.logger.info(f"📝 Web-Changelog exportiert: {repo_name} v{version}")
        except Exception as e:
            self.logger.warning(f"⚠️ Web-Export fehlgeschlagen: {e}")

    async def _send_to_internal_channel(self, embed: discord.Embed, repo_name: str) -> None:
        """Sende technische Notification an internen Channel."""
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if not internal_channel:
            return

        try:
            description_chunks = self._split_embed_description(embed.description or "")

            if len(description_chunks) <= 1:
                await internal_channel.send(embed=embed)
            else:
                for i, chunk in enumerate(description_chunks):
                    embed_copy = discord.Embed(
                        title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                        url=embed.url,
                        color=embed.color,
                        description=chunk,
                        timestamp=embed.timestamp
                    )
                    if i == 0:
                        embed_copy.set_author(name=embed.author.name)
                        for field in embed.fields:
                            embed_copy.add_field(name=field.name, value=field.value, inline=field.inline)
                    await internal_channel.send(embed=embed_copy)

            self.logger.info(f"📢 Technische Patch Notes für {repo_name} im internen Channel gesendet.")
        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden der Push-Benachrichtigung: {e}")

    async def _send_to_customer_channels(self, embed: discord.Embed, repo_name: str,
                                          project_config: Dict, version: Optional[str]) -> None:
        """Sende Patch Notes an Customer-Channel mit Feedback-Collection."""
        customer_channel_id = project_config.get('update_channel_id')
        if not customer_channel_id:
            return

        customer_channel = self.bot.get_channel(customer_channel_id)
        if not customer_channel:
            self.logger.warning(f"⚠️ Kunden-Update Channel {customer_channel_id} für {repo_name} nicht gefunden.")
            return

        try:
            description_chunks = self._split_embed_description(embed.description or "")
            sent_message = None

            if len(description_chunks) <= 1:
                sent_message = await customer_channel.send(embed=embed)
            else:
                for i, chunk in enumerate(description_chunks):
                    embed_copy = discord.Embed(
                        title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                        url=embed.url,
                        color=embed.color,
                        description=chunk,
                        timestamp=embed.timestamp
                    )
                    if i == len(description_chunks) - 1 and embed.footer:
                        embed_copy.set_footer(text=embed.footer.text)
                    message = await customer_channel.send(embed=embed_copy)
                    if i == 0:
                        sent_message = message

            self.logger.info(f"📢 Patch Notes für {repo_name} im Kunden-Channel gesendet.")

            # Feedback-Collection aktivieren
            if sent_message and self.feedback_collector and version:
                try:
                    await self.feedback_collector.track_patch_notes_message(
                        message=sent_message,
                        project=repo_name,
                        version=version,
                        add_feedback_button=True,
                    )
                    self.logger.info(f"👍 Feedback collection aktiviert für {repo_name} v{version}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Feedback collection fehlgeschlagen: {e}")

        except Exception as e:
            self.logger.error(f"❌ Fehler beim Senden im Kunden-Channel: {e}")

    async def _send_internal_only(self, repo_name: str, repo_url: str, branch: str,
                                   pusher: str, commits: list, color: int) -> None:
        """Sende nur interne Notification (wenn Commits gebatcht werden)."""
        internal_channel = self.bot.get_channel(self.deployment_channel_id)
        if not internal_channel:
            return

        commits_url = f"{repo_url}/commits/{branch}" if repo_url else None

        embed = discord.Embed(
            title=f"📦 Gesammelt: {repo_name}",
            url=commits_url,
            color=color,
            timestamp=datetime.now(timezone.utc),
            description=(
                f"**{len(commits)}** Commit(s) von **{pusher}** gesammelt.\n"
                f"Wird mit dem nächsten Release veröffentlicht."
            )
        )
        embed.add_field(name="Branch", value=branch, inline=True)

        # Zeige Batch-Status
        if hasattr(self, 'patch_notes_batcher') and self.patch_notes_batcher:
            summary = self.patch_notes_batcher.get_pending_summary()
            if repo_name in summary:
                info = summary[repo_name]
                embed.add_field(
                    name="📊 Batch",
                    value=f"{info['count']} ausstehend (Release bei {self.patch_notes_batcher.batch_threshold})",
                    inline=True
                )

        for commit in commits[:5]:
            # Zeige Commit-Titel im Embed
            pass  # Wird über description schon abgedeckt

        try:
            await internal_channel.send(embed=embed)
            self.logger.info(f"📦 Batch-Notification für {repo_name} gesendet")
        except Exception as e:
            self.logger.error(f"❌ Fehler bei Batch-Notification: {e}")

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

        if len(error) > 500:
            error = error[:497] + "..."
        embed.add_field(name="Exception", value=f"```{error}```", inline=False)

        await channel.send(embed=embed)

    async def _send_external_git_notifications(self, repo_name: str, embed: discord.Embed,
                                                project_config: Dict, version: str = None):
        """
        Send Git push notifications to external servers (customer guilds)
        AND activate feedback collection for AI learning.
        """
        external_notifs = project_config.get('external_notifications', [])
        if not external_notifs:
            return

        for notif_config in external_notifs:
            if not notif_config.get('enabled', False):
                continue

            notify_on = notif_config.get('notify_on', {})
            if not notify_on.get('git_push', True):
                continue

            channel_id = notif_config.get('channel_id')
            if not channel_id:
                continue

            try:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    self.logger.warning(f"⚠️ External channel {channel_id} not found for {repo_name}")
                    continue

                description_chunks = self._split_embed_description(embed.description or "")
                sent_message = None

                if len(description_chunks) <= 1:
                    sent_message = await channel.send(embed=embed)
                else:
                    for i, chunk in enumerate(description_chunks):
                        embed_copy = discord.Embed(
                            title=f"{embed.title} (Teil {i+1}/{len(description_chunks)})" if i > 0 else embed.title,
                            url=embed.url,
                            color=embed.color,
                            description=chunk,
                            timestamp=embed.timestamp
                        )
                        if i == len(description_chunks) - 1 and embed.footer:
                            embed_copy.set_footer(text=embed.footer.text)
                        message = await channel.send(embed=embed_copy)
                        if i == 0:
                            sent_message = message

                self.logger.info(f"📤 Sent git update for {repo_name} to external server")

                # ACTIVATE FEEDBACK COLLECTION
                if sent_message and self.feedback_collector and version:
                    try:
                        await self.feedback_collector.track_patch_notes_message(
                            message=sent_message,
                            project=repo_name,
                            version=version,
                            add_feedback_button=True,
                        )
                        self.logger.info(f"👍 Feedback collection activated for {repo_name} v{version}")
                    except Exception as e:
                        self.logger.warning(f"⚠️ Could not activate feedback collection: {e}")

            except Exception as e:
                self.logger.error(f"❌ Failed to send external git notification for {repo_name}: {e}")
