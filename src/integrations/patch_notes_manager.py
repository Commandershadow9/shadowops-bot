"""
Advanced Patch Notes Management System.

Features:
- CHANGELOG-based AI generation
- Two-pass review system with Discord approval
- Automatic Major/Minor release detection
- Fallback to manual scripts
"""

import asyncio
import logging
import discord
from discord import ui
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from utils.changelog_parser import get_changelog_parser
from integrations.ai_service import AIService

logger = logging.getLogger('shadowops')


class PatchNotesApprovalView(ui.View):
    """Discord View for patch notes approval."""

    def __init__(self, manager: 'PatchNotesManager', draft_embed: discord.Embed, project_name: str, version: str):
        super().__init__(timeout=1800)  # 30 minutes
        self.manager = manager
        self.draft_embed = draft_embed
        self.project_name = project_name
        self.version = version
        self.approved = None
        self.use_manual = False

    @ui.button(label="✅ Approve & Post", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        """Approve and post the AI-generated notes."""
        await interaction.response.defer()

        self.approved = True
        self.stop()

        # Post the notes
        await self.manager.post_patch_notes(self.project_name, self.draft_embed)

        # Update approval message
        await interaction.followup.send("✅ **Patch notes approved and posted!**", ephemeral=True)

    @ui.button(label="✏️ Use Manual Script", style=discord.ButtonStyle.primary)
    async def manual_button(self, interaction: discord.Interaction, button: ui.Button):
        """Use manual/template script instead."""
        await interaction.response.defer()

        self.use_manual = True
        self.stop()

        await interaction.followup.send(
            f"✏️ **Using manual script for {self.project_name} v{self.version}**\n"
            f"Run: `scripts/post_improved_{self.project_name}_notes.py`",
            ephemeral=True
        )

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """Cancel patch notes posting."""
        await interaction.response.defer()

        self.approved = False
        self.stop()

        await interaction.followup.send("❌ **Patch notes posting cancelled**", ephemeral=True)


class PatchNotesManager:
    """
    Manages patch notes generation and posting with review system.
    """

    def __init__(self, bot: discord.Client, ai_service: AIService):
        self.bot = bot
        self.ai_service = ai_service
        self.pending_approvals: Dict[str, PatchNotesApprovalView] = {}

    async def handle_git_push(self, project_name: str, project_config: Dict[str, Any],
                              commits: list, repo_name: str) -> None:
        """
        Handle git push event with advanced patch notes generation.

        Process:
        1. Detect version from commits or CHANGELOG
        2. Determine if Major/Minor release
        3. Parse CHANGELOG for details
        4. Generate AI draft
        5. Send for approval (if configured)
        6. Post to Discord
        """
        logger.info(f"🎯 Advanced patch notes generation for {project_name}")

        # Get project path
        project_path = Path(project_config.get('path', ''))
        if not project_path.exists():
            logger.error(f"Project path not found: {project_path}")
            return

        # Get CHANGELOG parser
        parser = get_changelog_parser(project_path)

        # Detect version
        version = self._detect_version(commits, parser)
        if not version:
            logger.warning(f"Could not detect version for {project_name} - using commit-based notes")
            return await self._fallback_commit_based(project_name, project_config, commits, repo_name)

        logger.info(f"📌 Detected version: {version}")

        # Check if major release
        is_major = parser.is_major_release(version)
        logger.info(f"🔍 Release type: {'MAJOR' if is_major else 'MINOR/PATCH'}")

        # Get CHANGELOG section
        version_data = parser.get_version_section(version)
        if not version_data:
            logger.warning(f"Version {version} not found in CHANGELOG - using commits")
            return await self._fallback_commit_based(project_name, project_config, commits, repo_name)

        # Generate patch notes
        if is_major:
            # Major release: Use CHANGELOG-based with review
            await self._major_release_flow(project_name, project_config, version, version_data, parser)
        else:
            # Minor/Patch: Use improved AI (CHANGELOG + commits)
            await self._minor_release_flow(project_name, project_config, version, version_data, commits)

    def _detect_version(self, commits: list, parser) -> Optional[str]:
        """Detect version from commits or CHANGELOG."""
        # Try to find version in commit messages
        import re

        for commit in commits:
            msg = commit.get('message', '')
            # Match patterns like: "v2.3.0", "Version 2.3.0", "Release 2.3.0"
            match = re.search(r'v?(?:ersion|elease)?\s*([0-9]+\.[0-9]+\.[0-9]+)', msg, re.IGNORECASE)
            if match:
                return match.group(1)

        # Fallback: Get latest from CHANGELOG
        return parser.get_latest_version()

    async def _major_release_flow(self, project_name: str, project_config: Dict,
                                   version: str, version_data: Dict, parser) -> None:
        """Handle major release with CHANGELOG-based AI and review."""
        logger.info(f"🚀 Major release detected - using CHANGELOG-based generation with review")

        # Format CHANGELOG for Discord
        discord_data = parser.format_for_discord(version_data, max_fields=10)

        # Use AI to improve formatting (optional)
        if project_config.get('patch_notes', {}).get('use_ai', False):
            discord_data = await self._ai_enhance_formatting(version_data, discord_data, project_name)

        # Create embed
        embed = self._create_embed(discord_data, project_config)

        # Send for approval
        await self._send_for_approval(project_name, version, embed, project_config)

    async def _minor_release_flow(self, project_name: str, project_config: Dict,
                                   version: str, version_data: Dict, commits: list) -> None:
        """Handle minor/patch release with AI (CHANGELOG + commits)."""
        logger.info(f"🔄 Minor/Patch release - using hybrid AI generation")

        # Combine CHANGELOG + commits for AI
        context = f"# CHANGELOG Section\n\n{version_data['content']}\n\n"
        context += f"# Commits\n\n"
        for commit in commits:
            context += f"- {commit.get('message', '')}\n"

        # Generate with AI
        patch_notes_config = project_config.get('patch_notes', {})
        language = patch_notes_config.get('language', 'en')

        prompt = self._create_changelog_format_prompt(context, language, project_name)

        try:
            ai_text = await self.ai_service.generate_raw_text(prompt, model_pref='llama3.1')

            if ai_text:
                # Create embed
                embed = discord.Embed(
                    title=f"✨ Updates for {project_name}",
                    description=f"**Version {version}** 🚀",
                    color=project_config.get('color', 3447003),
                    timestamp=datetime.utcnow()
                )

                # Parse AI response and add as field
                embed.add_field(name="", value=ai_text[:4096], inline=False)

                # Post directly (no approval for minor releases)
                await self.post_patch_notes(project_name, embed, project_config)

        except Exception as e:
            logger.error(f"AI generation failed: {e}", exc_info=True)
            await self._fallback_commit_based(project_name, project_config, commits, project_name)

    async def _ai_enhance_formatting(self, version_data: Dict, discord_data: Dict, project_name: str) -> Dict:
        """Use AI to enhance Discord formatting (not summarize!)."""
        prompt = f"""You are formatting CHANGELOG content for Discord.

DO NOT summarize or omit details. DO NOT shorten content.
ONLY improve the formatting for better readability in Discord.

Tasks:
1. Keep ALL information from the CHANGELOG
2. Use better Discord markdown (bold, lists, code blocks)
3. Split long sections if needed
4. Add relevant emojis (sparingly)

CHANGELOG Content:
{version_data['content']}

Return the formatted content ready for Discord embed fields."""

        try:
            enhanced = await self.ai_service.generate_raw_text(prompt, model_pref='llama3.1')
            if enhanced:
                # Update discord_data with enhanced formatting
                # (Implementation depends on AI response structure)
                pass
        except Exception as e:
            logger.warning(f"AI enhancement failed, using original: {e}")

        return discord_data

    def _create_changelog_format_prompt(self, context: str, language: str, project_name: str) -> str:
        """Create prompt for CHANGELOG-based formatting."""
        if language == 'de':
            return f"""Du formatierst Patch Notes für {project_name}.

WICHTIG: Du DARFST NICHT zusammenfassen oder Details weglassen!
Deine Aufgabe: Formatierung für Discord verbessern, ALLE Informationen behalten.

Context:
{context}

Erstelle Discord-formatierte Patch Notes mit:
- Alle Features und Änderungen aus dem CHANGELOG
- Bullet Points und Unterpunkte
- Passende Emojis (sparsam)
- Gute Strukturierung

Maximal 3900 Zeichen (Discord Limit)."""

        # English version
        return f"""You are formatting patch notes for {project_name}.

IMPORTANT: DO NOT summarize or omit details!
Your task: Improve formatting for Discord, keep ALL information.

Context:
{context}

Create Discord-formatted patch notes with:
- All features and changes from CHANGELOG
- Bullet points and sub-points
- Appropriate emojis (sparingly)
- Good structure

Maximum 3900 characters (Discord limit)."""

    def _create_embed(self, discord_data: Dict, project_config: Dict) -> discord.Embed:
        """Create Discord embed from formatted data."""
        embed = discord.Embed(
            title=discord_data['title'],
            description=discord_data['description'],
            color=project_config.get('color', 3447003),
            timestamp=datetime.utcnow()
        )

        for field in discord_data['fields']:
            embed.add_field(
                name=field['name'],
                value=field['value'],
                inline=False
            )

        return embed

    async def _send_for_approval(self, project_name: str, version: str,
                                  embed: discord.Embed, project_config: Dict) -> None:
        """Send patch notes draft for approval."""
        # Get internal notification channel
        from src.utils.config import get_config
        config = get_config()

        internal_channel_id = config.get_channel_for_alert('git_push', project_name)
        if not internal_channel_id:
            logger.warning("No internal channel for approval - posting directly")
            return await self.post_patch_notes(project_name, embed, project_config)

        channel = self.bot.get_channel(internal_channel_id)
        if not channel:
            logger.warning(f"Internal channel {internal_channel_id} not found")
            return await self.post_patch_notes(project_name, embed, project_config)

        # Create approval view
        view = PatchNotesApprovalView(self, embed, project_name, version)
        self.pending_approvals[f"{project_name}:{version}"] = view

        # Send draft with approval buttons
        await channel.send(
            f"📝 **Patch Notes Draft for {project_name} v{version}**\n"
            f"Please review and approve:",
            embed=embed,
            view=view
        )

        logger.info(f"✅ Patch notes draft sent for approval: {project_name} v{version}")

    async def post_patch_notes(self, project_name: str, embed: discord.Embed,
                                project_config: Optional[Dict] = None) -> None:
        """Post patch notes to external channels."""
        if project_config is None:
            from src.utils.config import get_config
            config = get_config()
            project_config = config.get_project_config(project_name)

        if not project_config:
            logger.error(f"Project config not found: {project_name}")
            return

        # Get external notification channels
        external_notifications = project_config.get('external_notifications', [])

        for notif in external_notifications:
            if not notif.get('enabled', True):
                continue

            if not notif.get('notify_on', {}).get('git_push', False):
                continue

            guild_id = notif['guild_id']
            channel_id = notif['channel_id']

            guild = self.bot.get_guild(guild_id)
            if not guild:
                logger.warning(f"Guild {guild_id} not found")
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                logger.warning(f"Channel {channel_id} not found")
                continue

            try:
                await channel.send(embed=embed)
                logger.info(f"✅ Patch notes posted to {guild.name} - #{channel.name}")
            except Exception as e:
                logger.error(f"Failed to post patch notes: {e}", exc_info=True)

    async def _fallback_commit_based(self, project_name: str, project_config: Dict,
                                     commits: list, repo_name: str) -> None:
        """Fallback to commit-based AI generation."""
        logger.info(f"⚠️ Falling back to commit-based generation")

        # Use existing AI generation from github_integration.py
        # (This will be called by the existing code path)
        pass


def get_patch_notes_manager(bot: discord.Client, ai_service: AIService) -> PatchNotesManager:
    """Get PatchNotesManager instance."""
    return PatchNotesManager(bot, ai_service)
