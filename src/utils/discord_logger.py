"""
Centralized Discord Logger
Routes log messages to appropriate Discord channels based on log category
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict
import discord

logger = logging.getLogger('shadowops.discord_logger')


class DiscordChannelLogger:
    """
    Central Discord logging system that routes messages to appropriate channels

    Channel Routing:
    - 🧠-ai-learning: AI Learning logs (Code Analyzer, Git History, etc.)
    - 🔧-code-fixes: Code Fixer vulnerability processing
    - ⚡-orchestrator: Batch event coordination
    - 📊-performance: Performance Monitor & Resource Anomalies
    - 🤖-auto-remediation-alerts: Auto-remediation status updates
    - ✋-auto-remediation-approvals: Human approval requests
    - 📊-auto-remediation-stats: Daily statistics
    - 🤖-bot-status: Bot startup & health checks
    """

    def __init__(self, bot=None, config=None):
        """
        Initialize Discord Channel Logger

        Args:
            bot: Discord bot instance
            config: Bot configuration
        """
        self.bot = bot
        self.config = config
        self.channels: Dict[str, int] = {}

        # Message queue for async sending with a max size to prevent memory exhaustion
        self.message_queue = asyncio.Queue(maxsize=1000)
        self.sender_task: Optional[asyncio.Task] = None
        self.running = False

    def set_bot(self, bot):
        """Set bot instance after initialization"""
        self.bot = bot
        self._load_channel_ids()

    def _load_channel_ids(self):
        """Load channel IDs from config"""
        if not self.config:
            return

        # Auto-Remediation channels with fallbacks to general channel map
        self.channels['alerts'] = self.config.alerts_channel
        self.channels['approvals'] = self.config.approvals_channel
        self.channels['stats'] = self.config.stats_channel
        self.channels['ai_learning'] = self.config.ai_learning_channel
        self.channels['code_fixes'] = self.config.code_fixes_channel
        self.channels['orchestrator'] = self.config.orchestrator_channel

        # Standard channels
        self.channels['performance'] = self.config.channels.get('performance')
        self.channels['bot_status'] = self.config.channels.get('bot_status')
        self.channels['critical'] = self.config.channels.get('critical')
        self.channels['docker'] = self.config.channels.get('docker')
        self.channels['fail2ban'] = self.config.channels.get('fail2ban')

        logger.debug(f"Loaded {len([v for v in self.channels.values() if v])} channel IDs")

    async def start(self):
        """Start async message sender task"""
        if self.running:
            return

        self.running = True
        self.sender_task = asyncio.create_task(self._message_sender_loop())
        logger.info("✅ Discord Channel Logger started")

    async def stop(self):
        """Stop async message sender task"""
        if not self.running:
            return

        self.running = False
        if self.sender_task:
            self.sender_task.cancel()
            try:
                await self.sender_task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Discord Channel Logger stopped")

    async def _message_sender_loop(self):
        """Background task that sends queued messages"""
        while self.running:
            try:
                # Get message from queue (wait max 1 second)
                try:
                    channel_key, message, embed = await asyncio.wait_for(
                        self.message_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Send message
                await self._send_to_channel(channel_key, message, embed)
                self.message_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in message sender loop: {e}")
                await asyncio.sleep(1)

    async def _send_to_channel(self, channel_key: str, message: str, embed: Optional[discord.Embed] = None):
        """Send message to Discord channel"""
        try:
            if not self.bot or self.bot.is_closed():
                logger.warning(f"Bot not ready or closed, cannot send message to {channel_key}")
                return

            channel_id = self.channels.get(channel_key)
            if not channel_id:
                logger.debug(f"Channel ID not found for '{channel_key}'")
                return

            channel = self.bot.get_channel(channel_id)
            if not channel:
                # Bot cache might not be ready, try fetching
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden):
                    logger.warning(f"Channel {channel_id} not found or no access for '{channel_key}'")
                    return
            
            # Send message
            if embed:
                await channel.send(content=message if message else None, embed=embed)
            elif message:
                await channel.send(message)

        except Exception as e:
            logger.error(f"Failed to send message to channel '{channel_key}': {e}")

    # =====================================
    # PUBLIC LOGGING METHODS
    # =====================================
    def _log(self, channel_key: str, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Generic internal logging method."""
        if not self.running:
            return
        
        message = self._add_severity_icon(message, severity)
        
        try:
            self.message_queue.put_nowait((channel_key, message, embed))
        except asyncio.QueueFull:
            logger.warning(f"Discord log queue is full! Dropping message for '{channel_key}'.")

    def _add_severity_icon(self, message: str, severity: Optional[str] = None) -> str:
        """Fügt Severity-Icon zur Message hinzu für bessere Übersichtlichkeit"""
        if not severity:
            return message

        icons = {
            'success': '✅',
            'info': 'ℹ️',
            'warning': '⚠️',
            'error': '❌',
            'critical': '🔴'
        }

        icon = icons.get(severity.lower(), '')
        if icon and not message.startswith(icon):
            return f"{icon} {message}"
        return message

    def log_ai_learning(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log AI Learning activity (Code Analyzer, Git History, etc.)"""
        self._log('ai_learning', message, embed, severity)

    def log_code_fix(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log Code Fixer activity (Vulnerability processing, fix generation)"""
        self._log('code_fixes', message, embed, severity)

    def log_orchestrator(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log Orchestrator activity (Batch processing, coordination)"""
        self._log('orchestrator', message, embed, severity)

    def log_performance(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log Performance Monitor activity (CPU, RAM anomalies)"""
        self._log('performance', message, embed, severity)

    def log_alert(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log auto-remediation alert"""
        self._log('alerts', message, embed, severity)

    def log_approval(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log approval request"""
        self._log('approvals', message, embed, severity)

    def log_stats(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log statistics"""
        self._log('stats', message, embed, severity)

    def log_bot_status(self, message: str, embed: Optional[discord.Embed] = None, severity: Optional[str] = None):
        """Log bot status (startup, health checks)"""
        self._log('bot_status', message, embed, severity)
