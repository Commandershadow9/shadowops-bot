"""
Queue Admin Commands - Discord Slash Commands for Queue Management

Provides administrative commands for managing the Ollama queue:
- /queue-status: View detailed queue status
- /queue-clear: Clear all pending requests
- /queue-pause: Pause request processing
- /queue-resume: Resume request processing
- /queue-cancel: Cancel a specific request
- /queue-stats: View detailed statistics
"""

import logging
from discord import app_commands
from discord.ext import commands
import discord
from typing import Optional

logger = logging.getLogger('shadowops')


class QueueAdminCommands(commands.Cog):
    """Admin commands for Ollama queue management."""

    def __init__(self, bot: commands.Bot, queue_manager, queue_dashboard, config):
        """
        Initialize queue admin commands.

        Args:
            bot: Discord bot instance
            queue_manager: OllamaQueueManager instance
            queue_dashboard: QueueDashboard instance
            config: Bot configuration
        """
        self.bot = bot
        self.queue_manager = queue_manager
        self.queue_dashboard = queue_dashboard
        self.config = config

        # Admin role/user restrictions
        permissions = getattr(config, 'permissions', {})
        self.admin_user_ids = permissions.get('admins', [])
        self.admin_role_ids = permissions.get('admin_roles', [])

        logger.info("✅ Queue Admin Commands loaded")

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        """Check if user has admin permissions."""
        # Check if user ID is in admin list
        if interaction.user.id in self.admin_user_ids:
            return True

        # Check if user has admin role
        if hasattr(interaction.user, 'roles'):
            for role in interaction.user.roles:
                if role.id in self.admin_role_ids:
                    return True

        # Check if user has administrator permission
        if interaction.user.guild_permissions.administrator:
            return True

        return False

    @app_commands.command(name="queue-status", description="View detailed Ollama queue status")
    async def queue_status(self, interaction: discord.Interaction):
        """Display detailed queue status."""
        await interaction.response.defer(ephemeral=True)

        status = self.queue_manager.get_queue_status()

        embed = discord.Embed(
            title="🔄 Ollama Queue Status",
            description="Detailed queue information",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )

        # Current request
        if status['current_request']:
            current = status['current_request']
            embed.add_field(
                name="⚙️ Currently Processing",
                value=f"**Type:** {current['task_type']}\n"
                      f"**Project:** {current['project']}\n"
                      f"**Priority:** {current['priority']}\n"
                      f"**Started:** <t:{int(discord.utils.utcnow().timestamp())}:R>",
                inline=False
            )
        else:
            embed.add_field(
                name="⚙️ Currently Processing",
                value="💤 No active requests",
                inline=False
            )

        # Queue info
        embed.add_field(
            name="📊 Queue Info",
            value=f"**Queue Size:** {status['queue_size']}\n"
                  f"**Pending:** {status['pending_count']}\n"
                  f"**Processing:** {status['processing_count']}\n"
                  f"**Completed:** {status['completed_count']}\n"
                  f"**Failed:** {status['failed_count']}",
            inline=True
        )

        # Worker status
        worker_status = "🟢 Running" if status['worker_running'] else "🔴 Stopped"
        embed.add_field(
            name="🤖 Worker Status",
            value=worker_status,
            inline=True
        )

        # Statistics
        stats = status['stats']
        embed.add_field(
            name="📈 Lifetime Statistics",
            value=f"**Total Processed:** {stats['total_processed']}\n"
                  f"**Total Failed:** {stats['total_failed']}\n"
                  f"**Total Cancelled:** {stats['total_cancelled']}\n"
                  f"**Avg Processing Time:** {stats['avg_processing_time']:.1f}s",
            inline=False
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="queue-clear", description="Clear all pending requests from queue (ADMIN)")
    async def queue_clear(self, interaction: discord.Interaction):
        """Clear all pending requests."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ This command requires administrator permissions.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        count = await self.queue_manager.clear_queue()

        embed = discord.Embed(
            title="🧹 Queue Cleared",
            description=f"Cleared **{count}** pending requests from queue.",
            color=discord.Color.orange()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Force dashboard update
        await self.queue_dashboard.force_update()

        logger.info(f"🧹 Queue cleared by {interaction.user.name} ({count} requests)")

    @app_commands.command(name="queue-pause", description="Pause queue processing (ADMIN)")
    async def queue_pause(self, interaction: discord.Interaction):
        """Pause the queue worker."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ This command requires administrator permissions.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        await self.queue_manager.pause_worker()

        embed = discord.Embed(
            title="⏸️ Queue Paused",
            description="Worker will finish current request and then pause.\n"
                       "New requests will queue but not process.",
            color=discord.Color.orange()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Force dashboard update
        await self.queue_dashboard.force_update()

        logger.info(f"⏸️ Queue paused by {interaction.user.name}")

    @app_commands.command(name="queue-resume", description="Resume queue processing (ADMIN)")
    async def queue_resume(self, interaction: discord.Interaction):
        """Resume the queue worker."""
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ This command requires administrator permissions.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        await self.queue_manager.resume_worker()

        embed = discord.Embed(
            title="▶️ Queue Resumed",
            description="Worker is now processing requests.",
            color=discord.Color.green()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Force dashboard update
        await self.queue_dashboard.force_update()

        logger.info(f"▶️ Queue resumed by {interaction.user.name}")

    @app_commands.command(name="queue-stats", description="View detailed queue statistics")
    async def queue_stats(self, interaction: discord.Interaction):
        """Display detailed statistics."""
        await interaction.response.defer(ephemeral=True)

        status = self.queue_manager.get_queue_status()
        stats = status['stats']

        embed = discord.Embed(
            title="📊 Queue Statistics",
            description="Detailed processing statistics",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )

        # Overall stats
        total = stats['total_processed'] + stats['total_failed'] + stats['total_cancelled']
        success_rate = (stats['total_processed'] / total * 100) if total > 0 else 0

        embed.add_field(
            name="📈 Overall Performance",
            value=f"**Total Requests:** {total}\n"
                  f"**Processed:** {stats['total_processed']} ({success_rate:.1f}%)\n"
                  f"**Failed:** {stats['total_failed']}\n"
                  f"**Cancelled:** {stats['total_cancelled']}\n"
                  f"**Avg Processing Time:** {stats['avg_processing_time']:.1f}s",
            inline=False
        )

        # Priority distribution
        priority_names = {1: "CRITICAL", 2: "HIGH", 3: "NORMAL", 4: "LOW"}
        priority_emojis = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🟢"}

        priority_text = ""
        for priority_value, count in sorted(stats['by_priority'].items()):
            if count > 0:
                name = priority_names.get(priority_value, f"Priority {priority_value}")
                emoji = priority_emojis.get(priority_value, "⚪")
                priority_text += f"{emoji} **{name}:** {count}\n"

        if priority_text:
            embed.add_field(
                name="🎯 By Priority",
                value=priority_text,
                inline=True
            )

        # Current queue state
        embed.add_field(
            name="📋 Current Queue",
            value=f"**Queue Size:** {status['queue_size']}\n"
                  f"**Pending:** {status['pending_count']}\n"
                  f"**Processing:** {status['processing_count']}",
            inline=True
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, queue_manager, queue_dashboard, config):
    """Setup function for loading the cog."""
    await bot.add_cog(QueueAdminCommands(bot, queue_manager, queue_dashboard, config))
