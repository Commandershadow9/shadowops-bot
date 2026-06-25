"""
Tests fuer die Changelog REST API auf dem Health-Server.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from aiohttp.test_utils import make_mocked_request

from src.utils.health_server import HealthCheckServer


@pytest.fixture
def mock_bot():
    """Erzeugt einen Mock-Bot mit den noetigsten Attributen."""
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.is_closed.return_value = False
    bot.guilds = []
    bot.latency = 0.05
    return bot


@pytest.fixture
def mock_changelog_db():
    """Erzeugt eine gemockte ChangelogDB."""
    db = AsyncMock()
    db.list_by_project.return_value = {
        'data': [{'project': 'zerodox', 'version': '2.9.1', 'title': 'Update',
                  'tldr': 'Test', 'published_at': '2026-03-12T00:00:00Z',
                  'content': 'Content', 'changes': [], 'stats': {},
                  'seo_keywords': [], 'language': 'de'}],
        'meta': {'page': 1, 'per_page': 10, 'total': 1, 'total_pages': 1},
    }
    db.get.return_value = {
        'project': 'zerodox', 'version': '2.9.1', 'title': 'Update',
        'tldr': 'Test', 'content': '# Changes', 'changes': [],
        'stats': {'commits': 5}, 'seo_keywords': ['test'],
        'seo_description': 'Test', 'language': 'de',
        'published_at': '2026-03-12T00:00:00Z',
    }
    db.upsert = AsyncMock()
    return db


@pytest.fixture
def health_server(mock_bot, mock_changelog_db):
    """Erstellt einen HealthCheckServer ohne echten TCP-Listener."""
    server = HealthCheckServer(
        bot=mock_bot,
        port=0,
        changelog_db=mock_changelog_db,
        api_key='test-secret-key',
    )
    return server


@pytest.fixture
def app(health_server):
    """aiohttp-App fuer socketfreie Handler-/Middleware-Tests."""
    return health_server._create_app()


async def _handle(app, method: str, path: str, headers=None):
    request = make_mocked_request(method, path, headers=headers or {}, app=app)
    return await app._handle(request)


def _json(response):
    return json.loads(response.text)


class _JsonRequest(SimpleNamespace):
    """Minimaler Request-Doppelgaenger fuer POST-Handler ohne Socket."""

    async def json(self):
        return self.payload


# --- Tests ---


@pytest.mark.asyncio
async def test_list_changelogs(app, mock_changelog_db):
    """GET /api/changelogs?project=zerodox liefert 200 mit Daten."""
    resp = await _handle(app, 'GET', '/api/changelogs?project=zerodox')
    assert resp.status == 200

    body = _json(resp)
    assert body['success'] is True
    assert len(body['data']) == 1
    assert body['data'][0]['project'] == 'zerodox'
    assert body['meta']['total'] == 1

    mock_changelog_db.list_by_project.assert_awaited_once_with(
        'zerodox', page=1, limit=10,
    )


@pytest.mark.asyncio
async def test_list_changelogs_requires_project(app):
    """GET /api/changelogs ohne project-Parameter gibt 400."""
    resp = await _handle(app, 'GET', '/api/changelogs')
    assert resp.status == 400

    body = _json(resp)
    assert 'error' in body


@pytest.mark.asyncio
async def test_get_changelog_detail(app, mock_changelog_db):
    """GET /api/changelogs/zerodox/2.9.1 liefert 200 mit Daten."""
    resp = await _handle(app, 'GET', '/api/changelogs/zerodox/2.9.1')
    assert resp.status == 200

    body = _json(resp)
    assert body['success'] is True
    assert body['data']['version'] == '2.9.1'
    assert body['data']['project'] == 'zerodox'

    mock_changelog_db.get.assert_awaited_once_with('zerodox', '2.9.1')


@pytest.mark.asyncio
async def test_get_changelog_not_found(app, mock_changelog_db):
    """GET /api/changelogs/zerodox/9.9.9 bei nicht-existentem Eintrag gibt 404."""
    mock_changelog_db.get.return_value = None

    resp = await _handle(app, 'GET', '/api/changelogs/zerodox/9.9.9')
    assert resp.status == 404

    body = _json(resp)
    assert 'error' in body


@pytest.mark.asyncio
async def test_post_requires_auth(health_server):
    """POST /api/changelogs ohne API-Key gibt 401."""
    resp = await health_server.changelog_create(
        _JsonRequest(
            headers={},
            payload={'project': 'test', 'version': '1.0.0', 'title': 'T', 'content': 'C'},
        )
    )
    assert resp.status == 401

    body = _json(resp)
    assert 'error' in body


@pytest.mark.asyncio
async def test_post_with_valid_key(health_server, mock_changelog_db):
    """POST /api/changelogs mit korrektem API-Key gibt 201."""
    payload = {
        'project': 'zerodox',
        'version': '3.0.0',
        'title': 'Neues Feature',
        'content': 'Beschreibung der Aenderungen',
    }
    resp = await health_server.changelog_create(
        _JsonRequest(headers={'X-API-Key': 'test-secret-key'}, payload=payload)
    )
    assert resp.status == 201

    body = _json(resp)
    assert body['success'] is True

    mock_changelog_db.upsert.assert_awaited_once()
    call_arg = mock_changelog_db.upsert.call_args[0][0]
    assert call_arg['project'] == 'zerodox'
    assert call_arg['version'] == '3.0.0'
    assert 'published_at' in call_arg


@pytest.mark.asyncio
async def test_post_validates_required_fields(health_server):
    """POST /api/changelogs ohne title gibt 400."""
    payload = {
        'project': 'zerodox',
        'version': '3.0.0',
        'content': 'Nur Content, kein Title',
    }
    resp = await health_server.changelog_create(
        _JsonRequest(headers={'X-API-Key': 'test-secret-key'}, payload=payload)
    )
    assert resp.status == 400

    body = _json(resp)
    assert 'error' in body
    assert 'title' in body['error']


@pytest.mark.asyncio
async def test_cors_headers_allowed_origin(health_server):
    """API echoed den Origin zurueck wenn er in CORS_ALLOWED_ORIGINS ist.

    Seit 2026-03-30 nutzt health_server.py eine Origin-Allowlist statt '*'
    (Defense-in-Depth — siehe CORS_ALLOWED_ORIGINS in health_server.py).
    """
    resp = await health_server._cors_middleware(
        SimpleNamespace(
            method='GET',
            path='/api/changelogs',
            headers={'Origin': 'https://zerodox.de'},
        ),
        lambda request: health_server.changelog_list(
            make_mocked_request('GET', '/api/changelogs?project=zerodox')
        ),
    )
    assert resp.status == 200
    assert resp.headers.get('Access-Control-Allow-Origin') == 'https://zerodox.de'


@pytest.mark.asyncio
async def test_cors_headers_unknown_origin(health_server):
    """Unbekannter Origin bekommt keinen CORS-Header (Allowlist greift)."""
    resp = await health_server._cors_middleware(
        SimpleNamespace(
            method='GET',
            path='/api/changelogs',
            headers={'Origin': 'https://evil.example.com'},
        ),
        lambda request: health_server.changelog_list(
            make_mocked_request('GET', '/api/changelogs?project=zerodox')
        ),
    )
    assert resp.status == 200
    # Unbekannter Origin -> kein Allow-Origin-Header
    assert resp.headers.get('Access-Control-Allow-Origin') is None


@pytest.mark.asyncio
async def test_rss_feed(app, mock_changelog_db):
    """GET /api/changelogs/feed?project=zerodox liefert RSS-XML."""
    resp = await _handle(app, 'GET', '/api/changelogs/feed?project=zerodox')
    assert resp.status == 200
    assert 'application/rss+xml' in resp.headers.get('Content-Type', '')

    text = resp.text
    assert '<rss' in text
    assert '<channel>' in text
    assert '<item>' in text
    assert 'zerodox' in text


@pytest.mark.asyncio
async def test_sitemap(app, mock_changelog_db):
    """GET /api/changelogs/sitemap liefert XML-Sitemap."""
    resp = await _handle(
        app,
        'GET',
        '/api/changelogs/sitemap?project=zerodox&base_url=https://zerodox.de',
    )
    assert resp.status == 200
    assert 'application/xml' in resp.headers.get('Content-Type', '')

    text = resp.text
    assert '<urlset' in text
    assert '<url>' in text
    assert 'https://zerodox.de/changelogs/2.9.1' in text
    assert '<changefreq>monthly</changefreq>' in text
    assert '<priority>0.6</priority>' in text


@pytest.mark.asyncio
async def test_existing_health_endpoint_still_works(app):
    """/health Endpoint funktioniert weiterhin."""
    resp = await _handle(app, 'GET', '/health')
    assert resp.status == 200

    body = _json(resp)
    assert body['status'] == 'healthy'
    assert body['service'] == 'shadowops-bot'
