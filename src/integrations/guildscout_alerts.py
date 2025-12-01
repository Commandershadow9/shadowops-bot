"""
GuildScout Alerts Integration for ShadowOps Bot
Receives and processes alerts from GuildScout bot
"""

import asyncio
import logging
import hmac
import hashlib
import json
from typing import Dict, Optional
from datetime import datetime
from aiohttp import web
import discord

logger = logging.getLogger('shadowops')


class GuildScoutAlertsHandler:
    """
    Webhook handler for GuildScout alerts

    Features:
    - Receives verification results
    - Receives error notifications
    - Receives health status changes
    - Posts formatted alerts to Discord
    """

    def __init__(self, bot, config: Dict):
        """
        Initialize GuildScout alerts handler

        Args:
            bot: Discord bot instance
            config: Configuration dictionary
        """
        self.bot = bot
        self.config = config
        self.logger = logger

        def _get_section(name: str, default=None):
            """Safely fetch config sections."""
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

        # Discord channel configuration
        channels_config = _get_section('channels', {})
        self.guildscout_channel_id = channels_config.get('guildscout', 0)

        # Webhook security
        guildscout_config = _get_section('guildscout', {})
        self.webhook_secret = guildscout_config.get('webhook_secret', '')

        # Webhook server (shares port with GitHub webhook)
        self.webhook_port = 9091
        self.app = None
        self.runner = None
        self.site = None

        self.logger.info("🔧 GuildScout Alerts Handler initialized")

    async def start_webhook_server(self):
        """Start the webhook HTTP server"""
        self.app = web.Application()
        self.app.router.add_post('/guildscout-alerts', self.webhook_handler)
        self.app.router.add_get('/health', self.health_check)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', self.webhook_port)
        await self.site.start()

        self.logger.info(f"✅ GuildScout Alerts webhook listening on port {self.webhook_port}")

    async def stop_webhook_server(self):
        """Stop the webhook server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self.logger.info("🛑 GuildScout Alerts webhook stopped")

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.Response(text='OK', status=200)

    def _verify_signature(self, payload: str, signature: str) -> bool:
        """
        Verify HMAC signature of webhook payload.

        Args:
            payload: JSON payload as string
            signature: Signature from X-Webhook-Signature header (format: sha256=<hex>)

        Returns:
            True if signature is valid
        """
        if not self.webhook_secret:
            # No secret configured, skip verification
            return True

        if not signature:
            return False

        # Extract hash from "sha256=<hash>" format
        if not signature.startswith('sha256='):
            return False

        received_hash = signature[7:]  # Remove "sha256=" prefix

        # Generate expected signature
        expected_signature = hmac.new(
            self.webhook_secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        )
        expected_hash = expected_signature.hexdigest()

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(received_hash, expected_hash)

    async def webhook_handler(self, request: web.Request) -> web.Response:
        """
        Handle incoming GuildScout alert webhooks

        Expected payload:
        {
            "source": "guildscout",
            "alert_type": "verification" | "error" | "health",
            "severity": "low" | "medium" | "high" | "critical",
            "title": "Alert title",
            "description": "Detailed description",
            "timestamp": "ISO8601 timestamp",
            "metadata": {...}
        }
        """
        try:
            # Read raw body for signature verification
            body = await request.read()
            body_str = body.decode('utf-8')

            # Verify signature if secret is configured
            signature = request.headers.get('X-Webhook-Signature', '')
            if not self._verify_signature(body_str, signature):
                self.logger.warning("❌ Invalid webhook signature - request rejected")
                return web.Response(text='Invalid signature', status=403)

            # Parse JSON payload
            payload = json.loads(body_str)

            # Validate payload
            if payload.get('source') != 'guildscout':
                return web.Response(text='Invalid source', status=400)

            alert_type = payload.get('alert_type')
            if alert_type not in ['verification', 'error', 'health']:
                return web.Response(text='Invalid alert_type', status=400)

            # Process alert asynchronously
            asyncio.create_task(self._process_alert(payload))

            return web.Response(text='Alert received', status=200)

        except Exception as e:
            self.logger.error(f"Error processing GuildScout alert: {e}", exc_info=True)
            return web.Response(text='Internal error', status=500)

    async def _process_alert(self, payload: Dict):
        """Process and post alert to Discord"""
        try:
            alert_type = payload.get('alert_type')
            severity = payload.get('severity', 'medium')
            title = payload.get('title', 'GuildScout Alert')
            description = payload.get('description', '')
            metadata = payload.get('metadata', {})

            # Get Discord channel
            channel = self.bot.get_channel(self.guildscout_channel_id)
            if not channel:
                self.logger.warning(f"GuildScout alert channel {self.guildscout_channel_id} not found")
                return

            # Create embed based on alert type
            if alert_type == 'verification':
                embed = self._create_verification_embed(title, description, metadata, severity)
            elif alert_type == 'error':
                embed = self._create_error_embed(title, description, metadata)
            elif alert_type == 'health':
                embed = self._create_health_embed(title, description, metadata, severity)
            else:
                embed = self._create_generic_embed(title, description, severity)

            # Send to Discord
            await channel.send(embed=embed)
            self.logger.info(f"✅ Posted GuildScout alert to Discord: {title}")

        except Exception as e:
            self.logger.error(f"Error posting GuildScout alert: {e}", exc_info=True)

    def _create_verification_embed(self, title: str, description: str, metadata: Dict, severity: str) -> discord.Embed:
        """Create embed for verification alerts"""
        # Color based on severity
        color_map = {
            'low': discord.Color.green(),
            'medium': discord.Color.orange(),
            'high': discord.Color.red(),
            'critical': discord.Color.dark_red()
        }
        color = color_map.get(severity, discord.Color.blue())

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )

        # Add metadata fields
        if metadata:
            if 'accuracy' in metadata:
                embed.add_field(name="Accuracy", value=f"{metadata['accuracy']:.1f}%", inline=True)
            if 'total_users' in metadata:
                embed.add_field(name="Users Checked", value=str(metadata['total_users']), inline=True)
            if 'healed' in metadata and metadata['healed'] > 0:
                embed.add_field(name="Healed", value=f"🩹 {metadata['healed']}", inline=True)

        embed.set_footer(text="GuildScout Verification System")
        return embed

    def _create_error_embed(self, title: str, description: str, metadata: Dict) -> discord.Embed:
        """Create embed for error alerts"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="GuildScout Error")
        return embed

    def _create_health_embed(self, title: str, description: str, metadata: Dict, severity: str) -> discord.Embed:
        """Create embed for health status alerts"""
        color = discord.Color.green() if severity == 'low' else discord.Color.red()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="GuildScout Health Monitor")
        return embed

    def _create_generic_embed(self, title: str, description: str, severity: str) -> discord.Embed:
        """Create generic embed for unknown alert types"""
        color_map = {
            'low': discord.Color.blue(),
            'medium': discord.Color.orange(),
            'high': discord.Color.red(),
            'critical': discord.Color.dark_red()
        }
        color = color_map.get(severity, discord.Color.blue())

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="GuildScout Alert")
        return embed


async def setup(bot, config):
    """Setup the GuildScout alerts handler"""
    handler = GuildScoutAlertsHandler(bot, config)
    await handler.start_webhook_server()
    return handler
