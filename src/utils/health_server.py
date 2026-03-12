"""
Simple HTTP Health Check Server for ShadowOps Bot
Provides a /health endpoint for monitoring systems
and a Changelog REST API for patch notes.
"""

import asyncio
import json
import logging
import socket
from aiohttp import web
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

logger = logging.getLogger("shadowops.health")


class HealthCheckServer:
    """Lightweight HTTP server for health checks and Changelog API"""

    def __init__(self, bot, port: int = 8766, changelog_db=None, api_key: str = ''):
        """
        Initialize health check server

        Args:
            bot: Discord bot instance
            port: Port to listen on (default: 8766)
            changelog_db: ChangelogDB Instanz fuer die Changelog-API (optional)
            api_key: API-Key fuer POST-Authentifizierung (optional)
        """
        self.bot = bot
        self.port = port
        self.changelog_db = changelog_db
        self.api_key = api_key
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_time = datetime.now(timezone.utc)

    def _create_app(self) -> web.Application:
        """Erstellt die aiohttp-Application mit allen Routen."""
        app = web.Application(middlewares=[self._cors_middleware])

        # Bestehende Health-Endpoints
        app.router.add_get('/health', self.health_check)
        app.router.add_get('/ping', self.ping)
        app.router.add_get('/status', self.detailed_status)

        # Changelog API
        app.router.add_get('/api/changelogs/feed', self.changelog_feed)
        app.router.add_get('/api/changelogs/sitemap', self.changelog_sitemap)
        app.router.add_get('/api/changelogs/{project}/{version}', self.changelog_detail)
        app.router.add_get('/api/changelogs', self.changelog_list)
        app.router.add_post('/api/changelogs', self.changelog_create)

        return app

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """CORS Middleware fuer /api/ Routen."""
        # Preflight OPTIONS Requests
        if request.method == 'OPTIONS' and request.path.startswith('/api/'):
            response = web.Response(status=204)
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
            return response

        response = await handler(request)

        # CORS-Header auf alle /api/ Responses
        if request.path.startswith('/api/'):
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'

        return response

    async def start(self):
        """Start the health check HTTP server"""
        try:
            self.app = self._create_app()

            self.runner = web.AppRunner(self.app)
            await self.runner.setup()

            self.site = web.TCPSite(
                self.runner, '0.0.0.0', self.port,
                reuse_address=True, reuse_port=True
            )
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

    # --- Bestehende Health-Endpoints ---

    async def health_check(self, request: web.Request) -> web.Response:
        """
        Health check endpoint
        Returns 200 if bot is healthy, 503 if not
        """
        is_healthy = (
            self.bot.is_ready() and
            not self.bot.is_closed()
        )

        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        response_data = {
            'status': 'healthy' if is_healthy else 'unhealthy',
            'service': 'shadowops-bot',
            'bot_ready': self.bot.is_ready(),
            'bot_closed': self.bot.is_closed(),
            'uptime_seconds': round(uptime, 2),
            'guilds': len(self.bot.guilds) if self.bot.is_ready() else 0,
            'latency_ms': round(self.bot.latency * 1000, 2) if self.bot.is_ready() else None,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        status_code = 200 if is_healthy else 503

        return web.json_response(response_data, status=status_code)

    async def ping(self, request: web.Request) -> web.Response:
        """Simple ping endpoint"""
        return web.json_response({
            'status': 'pong',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    async def detailed_status(self, request: web.Request) -> web.Response:
        """Detailed status with component information"""
        is_healthy = self.bot.is_ready() and not self.bot.is_closed()
        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()

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
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        status_code = 200 if is_healthy else 503

        return web.json_response(response_data, status=status_code)

    # --- Changelog API ---

    async def changelog_list(self, request: web.Request) -> web.Response:
        """
        GET /api/changelogs?project=xxx&page=1&limit=10
        Listet Changelogs eines Projekts paginiert auf.
        """
        if not self.changelog_db:
            return web.json_response(
                {"error": "Changelog-Dienst nicht verfuegbar"},
                status=503,
            )

        project = request.query.get('project')
        if not project:
            return web.json_response(
                {"error": "Query-Parameter 'project' ist erforderlich"},
                status=400,
            )

        try:
            page = max(1, int(request.query.get('page', '1')))
        except (ValueError, TypeError):
            page = 1

        try:
            limit = min(50, max(1, int(request.query.get('limit', '10'))))
        except (ValueError, TypeError):
            limit = 10

        result = await self.changelog_db.list_by_project(project, page=page, limit=limit)

        return web.json_response({
            "success": True,
            "data": result["data"],
            "meta": result["meta"],
        })

    async def changelog_detail(self, request: web.Request) -> web.Response:
        """
        GET /api/changelogs/{project}/{version}
        Einzelnen Changelog-Eintrag abrufen.
        """
        if not self.changelog_db:
            return web.json_response(
                {"error": "Changelog-Dienst nicht verfuegbar"},
                status=503,
            )

        project = request.match_info['project']
        version = request.match_info['version']

        entry = await self.changelog_db.get(project, version)
        if entry is None:
            return web.json_response(
                {"error": f"Changelog {project} v{version} nicht gefunden"},
                status=404,
            )

        return web.json_response({
            "success": True,
            "data": entry,
        })

    async def changelog_create(self, request: web.Request) -> web.Response:
        """
        POST /api/changelogs
        Neuen Changelog-Eintrag erstellen (erfordert API-Key).
        """
        # Auth pruefen
        provided_key = request.headers.get('X-API-Key', '')
        if not self.api_key or provided_key != self.api_key:
            return web.json_response(
                {"error": "Authentifizierung fehlgeschlagen"},
                status=401,
            )

        if not self.changelog_db:
            return web.json_response(
                {"error": "Changelog-Dienst nicht verfuegbar"},
                status=503,
            )

        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Ungueltiger JSON-Body"},
                status=400,
            )

        # Required Fields validieren
        required = ('project', 'version', 'title', 'content')
        missing = [f for f in required if not body.get(f)]
        if missing:
            return web.json_response(
                {"error": f"Fehlende Pflichtfelder: {', '.join(missing)}"},
                status=400,
            )

        # published_at setzen falls nicht vorhanden
        if 'published_at' not in body:
            body['published_at'] = datetime.now(timezone.utc).isoformat()

        await self.changelog_db.upsert(body)

        return web.json_response({"success": True}, status=201)

    async def changelog_feed(self, request: web.Request) -> web.Response:
        """
        GET /api/changelogs/feed?project=xxx&format=rss
        RSS 2.0 Feed fuer ein Projekt.
        """
        if not self.changelog_db:
            return web.json_response(
                {"error": "Changelog-Dienst nicht verfuegbar"},
                status=503,
            )

        project = request.query.get('project')
        if not project:
            return web.json_response(
                {"error": "Query-Parameter 'project' ist erforderlich"},
                status=400,
            )

        # Letzte 20 Eintraege fuer den Feed
        result = await self.changelog_db.list_by_project(project, page=1, limit=20)
        items = result["data"]

        # RSS 2.0 XML erstellen
        rss_items = []
        for item in items:
            pub_date = self._format_rss_date(item.get('published_at', ''))
            tldr = xml_escape(item.get('tldr', '') or item.get('title', ''))
            title = xml_escape(item.get('title', ''))
            version = xml_escape(item.get('version', ''))
            guid = f"{project}-{item.get('version', '')}"

            rss_items.append(
                f"    <item>\n"
                f"      <title>{title}</title>\n"
                f"      <link>/{project}/changelogs/{version}</link>\n"
                f"      <description>{tldr}</description>\n"
                f"      <pubDate>{pub_date}</pubDate>\n"
                f"      <guid>{xml_escape(guid)}</guid>\n"
                f"    </item>"
            )

        items_xml = "\n".join(rss_items)
        project_escaped = xml_escape(project)

        rss_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0">\n'
            '  <channel>\n'
            f'    <title>{project_escaped} Changelog</title>\n'
            f'    <description>Aktuelle Aenderungen fuer {project_escaped}</description>\n'
            f'    <language>de</language>\n'
            f'{items_xml}\n'
            '  </channel>\n'
            '</rss>'
        )

        return web.Response(
            text=rss_xml,
            content_type='application/rss+xml',
            charset='utf-8',
        )

    async def changelog_sitemap(self, request: web.Request) -> web.Response:
        """
        GET /api/changelogs/sitemap?project=xxx&base_url=https://example.com
        XML Sitemap Fragment fuer Changelog-Seiten.
        """
        if not self.changelog_db:
            return web.json_response(
                {"error": "Changelog-Dienst nicht verfuegbar"},
                status=503,
            )

        project = request.query.get('project')
        if not project:
            return web.json_response(
                {"error": "Query-Parameter 'project' ist erforderlich"},
                status=400,
            )

        base_url = request.query.get('base_url')
        if not base_url:
            return web.json_response(
                {"error": "Query-Parameter 'base_url' ist erforderlich"},
                status=400,
            )

        # Trailing Slash entfernen
        base_url = base_url.rstrip('/')

        # Alle Eintraege fuer die Sitemap (max 100)
        result = await self.changelog_db.list_by_project(project, page=1, limit=100)
        items = result["data"]

        url_entries = []
        for item in items:
            version = xml_escape(item.get('version', ''))
            published = item.get('published_at', '')
            # ISO-Datum fuer lastmod (nur Datumsteil)
            lastmod = published[:10] if published and len(published) >= 10 else ''

            url_entries.append(
                f"  <url>\n"
                f"    <loc>{xml_escape(base_url)}/changelogs/{version}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"    <changefreq>monthly</changefreq>\n"
                f"    <priority>0.6</priority>\n"
                f"  </url>"
            )

        urls_xml = "\n".join(url_entries)

        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'{urls_xml}\n'
            '</urlset>'
        )

        return web.Response(
            text=sitemap_xml,
            content_type='application/xml',
            charset='utf-8',
        )

    @staticmethod
    def _format_rss_date(iso_date: str) -> str:
        """Konvertiert ein ISO-Datum in RFC 822 Format fuer RSS."""
        if not iso_date:
            return ''
        try:
            # ISO-Format parsen
            dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
            return format_datetime(dt, usegmt=True)
        except (ValueError, TypeError):
            return iso_date
