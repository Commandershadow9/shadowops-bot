"""
Queue Dashboard - Discord Integration for Ollama Queue Visualization

Provides a Discord channel dashboard showing:
- Current request being processed
- Pending requests in queue
- Queue statistics
- Priority distribution
- Processing history

Updates automatically every 30 seconds or on queue changes.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
import discord
from discord.ext import tasks

from integrations.ollama_queue_manager import OllamaQueueManager, Priority

logger = logging.getLogger('shadowops')


class QueueDashboard:
    """
    Discord dashboard for monitoring the Ollama queue.

    Displays real-time queue status in a dedicated Discord channel.
    """

    def __init__(self, bot: discord.Client, queue_manager: OllamaQueueManager, channel_id: int):
        """
        Initialize the queue dashboard.

        Args:
            bot: Discord bot instance
            queue_manager: OllamaQueueManager instance
            channel_id: Discord channel ID for dashboard
        """
        self.bot = bot
        self.queue_manager = queue_manager
        self.channel_id = channel_id
        self.dashboard_message: Optional[discord.Message] = None

        # Start update loop
        self.update_loop.start()

        logger.info(f"✅ Queue Dashboard initialized (Channel: {channel_id})")

    @tasks.loop(seconds=30)
    async def update_loop(self):
        """Update dashboard every 30 seconds."""
        try:
            await self.update_dashboard()
        except Exception as e:
            logger.error(f"❌ Dashboard update failed: {e}", exc_info=True)

    @update_loop.before_loop
    async def before_update_loop(self):
        """Wait for bot to be ready before starting updates."""
        await self.bot.wait_until_ready()

        # Clean up old dashboard messages
        await self._cleanup_old_messages()

    async def _cleanup_old_messages(self):
        """Delete all old messages in the dashboard channel to keep it clean."""
        try:
            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                logger.warning(f"⚠️ Dashboard channel {self.channel_id} not found for cleanup")
                return

            # Delete all messages in the channel
            deleted_count = 0
            async for message in channel.history(limit=100):
                try:
                    await message.delete()
                    deleted_count += 1
                except discord.Forbidden:
                    logger.warning(f"⚠️ Missing permissions to delete messages in {channel.name}")
                    break
                except discord.NotFound:
                    # Message already deleted
                    pass
                except Exception as e:
                    logger.debug(f"Could not delete message: {e}")

            if deleted_count > 0:
                logger.info(f"🧹 Cleaned up {deleted_count} old dashboard messages in {channel.name}")
        except Exception as e:
            logger.error(f"❌ Failed to cleanup old messages: {e}", exc_info=True)

    async def update_dashboard(self):
        """Update the dashboard with current queue status."""
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(f"⚠️ Dashboard channel {self.channel_id} not found")
            return

        # Get queue status
        status = self.queue_manager.get_queue_status()

        # Build embed
        embed = self._build_dashboard_embed(status)

        # Update or create message
        try:
            if self.dashboard_message:
                # Edit existing message
                await self.dashboard_message.edit(embed=embed)
            else:
                # Create new message
                self.dashboard_message = await channel.send(embed=embed)
        except discord.NotFound:
            # Message was deleted, create new one
            self.dashboard_message = await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ Failed to update dashboard: {e}", exc_info=True)

    def _build_dashboard_embed(self, status: dict) -> discord.Embed:
        """Build dashboard embed from queue status."""
        # Choose color based on queue state
        if status['current_request']:
            color = discord.Color.blue()  # Processing
        elif status['queue_size'] > 0:
            color = discord.Color.orange()  # Waiting
        else:
            color = discord.Color.green()  # Idle

        embed = discord.Embed(
            title="🔄 Ollama Queue Dashboard",
            description="Live queue status and processing statistics",
            color=color,
            timestamp=datetime.utcnow()
        )

        # Current status
        if status['current_request']:
            current = status['current_request']
            priority_emoji = self._get_priority_emoji(current['priority'])
            elapsed = self._calculate_elapsed(current['started_at'])

            embed.add_field(
                name="⚙️ Currently Processing",
                value=f"{priority_emoji} **{current['task_type']}** - {current['project']}\n"
                      f"Started: <t:{int(datetime.fromisoformat(current['started_at']).timestamp())}:R>\n"
                      f"Elapsed: {elapsed}s",
                inline=False
            )
        else:
            embed.add_field(
                name="⚙️ Currently Processing",
                value="💤 Idle - No active requests",
                inline=False
            )

        # Queue summary
        queue_text = f"**Total in Queue:** {status['queue_size']}\n"
        queue_text += f"**Pending:** {status['pending_count']}\n"
        queue_text += f"**Processing:** {status['processing_count']}\n"
        queue_text += f"**Worker:** {'🟢 Running' if status['worker_running'] else '🔴 Stopped'}"

        embed.add_field(
            name="📊 Queue Summary",
            value=queue_text,
            inline=True
        )

        # Statistics
        stats = status['stats']
        stats_text = f"**Total Processed:** {stats['total_processed']}\n"
        stats_text += f"**Failed:** {stats['total_failed']}\n"
        stats_text += f"**Cancelled:** {stats['total_cancelled']}\n"
        stats_text += f"**Avg Time:** {stats['avg_processing_time']:.1f}s"

        embed.add_field(
            name="📈 Statistics",
            value=stats_text,
            inline=True
        )

        # Priority distribution
        if any(stats['by_priority'].values()):
            priority_text = ""
            for priority_value, count in sorted(stats['by_priority'].items()):
                if count > 0:
                    priority_name = Priority(priority_value).name
                    priority_emoji = self._get_priority_emoji(priority_value)
                    priority_text += f"{priority_emoji} **{priority_name}:** {count}\n"

            if priority_text:
                embed.add_field(
                    name="🎯 By Priority",
                    value=priority_text,
                    inline=True
                )

        # Pending requests preview
        if status['pending_requests']:
            pending_text = ""
            for i, req in enumerate(status['pending_requests'][:5], 1):
                priority_emoji = self._get_priority_emoji(req['priority'])
                pending_text += f"{i}. {priority_emoji} {req['task_type']} - {req['project']}\n"

            if len(status['pending_requests']) > 5:
                pending_text += f"*... and {len(status['pending_requests']) - 5} more*"

            embed.add_field(
                name="📋 Next in Queue",
                value=pending_text or "No pending requests",
                inline=False
            )

        # Footer
        embed.set_footer(text="Updates every 30 seconds • Use /queue commands to manage")

        return embed

    def _get_priority_emoji(self, priority: int) -> str:
        """Get emoji for priority level."""
        priority_emojis = {
            Priority.CRITICAL.value: "🔴",  # Red circle
            Priority.HIGH.value: "🟠",      # Orange circle
            Priority.NORMAL.value: "🟡",    # Yellow circle
            Priority.LOW.value: "🟢"        # Green circle
        }
        return priority_emojis.get(priority, "⚪")

    def _calculate_elapsed(self, started_at: str) -> int:
        """Calculate elapsed time in seconds."""
        try:
            start_time = datetime.fromisoformat(started_at)
            return int((datetime.utcnow() - start_time).total_seconds())
        except:
            return 0

    async def force_update(self):
        """Force an immediate dashboard update."""
        await self.update_dashboard()

    def stop(self):
        """Stop the dashboard update loop."""
        self.update_loop.cancel()
        logger.info("🛑 Queue Dashboard stopped")
