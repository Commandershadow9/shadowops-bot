"""
Simple HTTP Health Check Server for ShadowOps Bot
Provides a /health endpoint for monitoring systems
"""

import asyncio
import logging
from aiohttp import web
from datetime import datetime
from typing import Optional

logger = logging.getLogger("shadowops.health")


class HealthCheckServer:
    """Lightweight HTTP server for health checks"""

    def __init__(self, bot, port: int = 8766):
        """
        Initialize health check server

        Args:
            bot: Discord bot instance
            port: Port to listen on (default: 8766)
        """
        self.bot = bot
        self.port = port
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_time = datetime.utcnow()

    async def start(self):
        """Start the health check HTTP server"""
        try:
            self.app = web.Application()
            self.app.router.add_get('/health', self.health_check)
            self.app.router.add_get('/ping', self.ping)
            self.app.router.add_get('/status', self.detailed_status)

            self.runner = web.AppRunner(self.app)
            await self.runner.setup()

            self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
            await self.site.start()

            logger.info(f"✅ Health check server started on port {self.port}")
        except Exception as e:
            logger.error(f"❌ Failed to start health check server: {e}")
            raise

    async def stop(self):
        """Stop the health check HTTP server"""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("🛑 Health check server stopped")

    async def health_check(self, request: web.Request) -> web.Response:
        """
        Health check endpoint
        Returns 200 if bot is healthy, 503 if not
        """
        is_healthy = (
            self.bot.is_ready() and
            not self.bot.is_closed()
        )

        uptime = (datetime.utcnow() - self.start_time).total_seconds()

        response_data = {
            'status': 'healthy' if is_healthy else 'unhealthy',
            'service': 'shadowops-bot',
            'bot_ready': self.bot.is_ready(),
            'bot_closed': self.bot.is_closed(),
            'uptime_seconds': round(uptime, 2),
            'guilds': len(self.bot.guilds) if self.bot.is_ready() else 0,
            'latency_ms': round(self.bot.latency * 1000, 2) if self.bot.is_ready() else None,
            'timestamp': datetime.utcnow().isoformat()
        }

        status_code = 200 if is_healthy else 503

        return web.json_response(response_data, status=status_code)

    async def ping(self, request: web.Request) -> web.Response:
        """Simple ping endpoint"""
        return web.json_response({
            'status': 'pong',
            'timestamp': datetime.utcnow().isoformat()
        })

    async def detailed_status(self, request: web.Request) -> web.Response:
        """Detailed status with component information"""
        is_healthy = self.bot.is_ready() and not self.bot.is_closed()
        uptime = (datetime.utcnow() - self.start_time).total_seconds()

        # Gather component status
        components = {}

        # Check if core components are initialized
        if getattr(self.bot, 'ai_service', None):
            components['ai_service'] = 'active'
        if getattr(self.bot, 'self_healing', None):
            components['self_healing'] = 'active'
        if getattr(self.bot, 'project_monitor', None):
            components['project_monitor'] = 'active'
            if hasattr(self.bot.project_monitor, 'projects'):
                components['monitored_projects'] = len(self.bot.project_monitor.projects)
        if getattr(self.bot, 'deployment_manager', None):
            components['deployment_manager'] = 'active'

        response_data = {
            'status': 'healthy' if is_healthy else 'unhealthy',
            'service': 'shadowops-bot',
            'version': '3.1.0',
            'bot_ready': self.bot.is_ready(),
            'bot_closed': self.bot.is_closed(),
            'uptime_seconds': round(uptime, 2),
            'guilds': len(self.bot.guilds) if self.bot.is_ready() else 0,
            'latency_ms': round(self.bot.latency * 1000, 2) if self.bot.is_ready() else None,
            'components': components,
            'timestamp': datetime.utcnow().isoformat()
        }

        status_code = 200 if is_healthy else 503

        return web.json_response(response_data, status=status_code)
