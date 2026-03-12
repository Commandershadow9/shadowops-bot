"""
Tests fuer die Changelog REST API auf dem Health-Server.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

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
async def client(mock_bot, mock_changelog_db):
    """Erstellt einen aiohttp TestClient mit dem HealthCheckServer."""
    server = HealthCheckServer(
        bot=mock_bot,
        port=0,
        changelog_db=mock_changelog_db,
        api_key='test-secret-key',
    )
    app = server._create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
async def client_no_db(mock_bot):
    """TestClient OHNE changelog_db (fuer 503-Tests)."""
    server = HealthCheckServer(bot=mock_bot, port=0)
    app = server._create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


# --- Tests ---


@pytest.mark.asyncio
async def test_list_changelogs(client, mock_changelog_db):
    """GET /api/changelogs?project=zerodox liefert 200 mit Daten."""
    resp = await client.get('/api/changelogs?project=zerodox')
    assert resp.status == 200

    body = await resp.json()
    assert body['success'] is True
    assert len(body['data']) == 1
    assert body['data'][0]['project'] == 'zerodox'
    assert body['meta']['total'] == 1

    mock_changelog_db.list_by_project.assert_awaited_once_with(
        'zerodox', page=1, limit=10,
    )


@pytest.mark.asyncio
async def test_list_changelogs_requires_project(client):
    """GET /api/changelogs ohne project-Parameter gibt 400."""
    resp = await client.get('/api/changelogs')
    assert resp.status == 400

    body = await resp.json()
    assert 'error' in body


@pytest.mark.asyncio
async def test_get_changelog_detail(client, mock_changelog_db):
    """GET /api/changelogs/zerodox/2.9.1 liefert 200 mit Daten."""
    resp = await client.get('/api/changelogs/zerodox/2.9.1')
    assert resp.status == 200

    body = await resp.json()
    assert body['success'] is True
    assert body['data']['version'] == '2.9.1'
    assert body['data']['project'] == 'zerodox'

    mock_changelog_db.get.assert_awaited_once_with('zerodox', '2.9.1')


@pytest.mark.asyncio
async def test_get_changelog_not_found(client, mock_changelog_db):
    """GET /api/changelogs/zerodox/9.9.9 bei nicht-existentem Eintrag gibt 404."""
    mock_changelog_db.get.return_value = None

    resp = await client.get('/api/changelogs/zerodox/9.9.9')
    assert resp.status == 404

    body = await resp.json()
    assert 'error' in body


@pytest.mark.asyncio
async def test_post_requires_auth(client):
    """POST /api/changelogs ohne API-Key gibt 401."""
    resp = await client.post(
        '/api/changelogs',
        json={'project': 'test', 'version': '1.0.0', 'title': 'T', 'content': 'C'},
    )
    assert resp.status == 401

    body = await resp.json()
    assert 'error' in body


@pytest.mark.asyncio
async def test_post_with_valid_key(client, mock_changelog_db):
    """POST /api/changelogs mit korrektem API-Key gibt 201."""
    payload = {
        'project': 'zerodox',
        'version': '3.0.0',
        'title': 'Neues Feature',
        'content': 'Beschreibung der Aenderungen',
    }
    resp = await client.post(
        '/api/changelogs',
        json=payload,
        headers={'X-API-Key': 'test-secret-key'},
    )
    assert resp.status == 201

    body = await resp.json()
    assert body['success'] is True

    mock_changelog_db.upsert.assert_awaited_once()
    call_arg = mock_changelog_db.upsert.call_args[0][0]
    assert call_arg['project'] == 'zerodox'
    assert call_arg['version'] == '3.0.0'
    assert 'published_at' in call_arg


@pytest.mark.asyncio
async def test_post_validates_required_fields(client):
    """POST /api/changelogs ohne title gibt 400."""
    payload = {
        'project': 'zerodox',
        'version': '3.0.0',
        'content': 'Nur Content, kein Title',
    }
    resp = await client.post(
        '/api/changelogs',
        json=payload,
        headers={'X-API-Key': 'test-secret-key'},
    )
    assert resp.status == 400

    body = await resp.json()
    assert 'error' in body
    assert 'title' in body['error']


@pytest.mark.asyncio
async def test_cors_headers(client):
    """API-Responses enthalten CORS-Header."""
    resp = await client.get('/api/changelogs?project=zerodox')
    assert resp.status == 200
    assert resp.headers.get('Access-Control-Allow-Origin') == '*'


@pytest.mark.asyncio
async def test_rss_feed(client, mock_changelog_db):
    """GET /api/changelogs/feed?project=zerodox liefert RSS-XML."""
    resp = await client.get('/api/changelogs/feed?project=zerodox')
    assert resp.status == 200
    assert 'application/rss+xml' in resp.headers.get('Content-Type', '')

    text = await resp.text()
    assert '<rss' in text
    assert '<channel>' in text
    assert '<item>' in text
    assert 'zerodox' in text


@pytest.mark.asyncio
async def test_sitemap(client, mock_changelog_db):
    """GET /api/changelogs/sitemap liefert XML-Sitemap."""
    resp = await client.get(
        '/api/changelogs/sitemap?project=zerodox&base_url=https://zerodox.de'
    )
    assert resp.status == 200
    assert 'application/xml' in resp.headers.get('Content-Type', '')

    text = await resp.text()
    assert '<urlset' in text
    assert '<url>' in text
    assert 'https://zerodox.de/changelogs/2.9.1' in text
    assert '<changefreq>monthly</changefreq>' in text
    assert '<priority>0.6</priority>' in text


@pytest.mark.asyncio
async def test_existing_health_endpoint_still_works(client):
    """/health Endpoint funktioniert weiterhin."""
    resp = await client.get('/health')
    assert resp.status == 200

    body = await resp.json()
    assert body['status'] == 'healthy'
    assert body['service'] == 'shadowops-bot'
