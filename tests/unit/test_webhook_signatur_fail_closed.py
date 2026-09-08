"""
Webhook-Signaturprüfung: fail-closed statt fail-open.

Befund aus der Sicherheitsbewertung vom 08.09.2026: Die Prüfung lief nur unter
`if self.webhook_secret:` — bei fehlender oder leerer Konfiguration verarbeitete
der Bot jede unsignierte Anfrage weiter. Der Prozess läuft unter einem Konto mit
weitreichenden Hostrechten und der Port ist öffentlich freigegeben; ein
Konfigurationsfehler hätte damit unmittelbar Codeausführung bedeutet.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.integrations.github_integration import GitHubIntegration
from src.utils.config import Config


def _konfig(webhook_secret):
    cfg = MagicMock(spec=Config)
    cfg.github = {
        'enabled': True,
        'webhook_secret': webhook_secret,
        'webhook_port': 8080,
        'auto_deploy': False,
        'deploy_branches': ['main'],
    }
    cfg.channels = {'deployment_log': 1, 'code_fixes': 1}
    cfg.discord = {'guild_id': 1}
    return cfg


def _bot():
    bot = Mock()
    bot.get_channel = Mock(return_value=Mock(send=AsyncMock()))
    return bot


def _integration(webhook_secret):
    integration = GitHubIntegration(_bot(), _konfig(webhook_secret))
    integration.bot_ready = True
    integration.pending_webhooks = []
    integration.event_handlers = {'push': AsyncMock()}
    return integration


def _anfrage(rumpf: bytes, signatur=None, event='push', delivery='d-1'):
    """Minimaler Ersatz für aiohttp.web.Request — gelesen werden nur read() und headers."""
    kopfzeilen = {'X-GitHub-Event': event, 'X-GitHub-Delivery': delivery}
    if signatur is not None:
        kopfzeilen['X-Hub-Signature-256'] = signatur
    anfrage = Mock()
    anfrage.read = AsyncMock(return_value=rumpf)
    anfrage.headers = kopfzeilen
    return anfrage


def _signiere(rumpf: bytes, geheimnis: str) -> str:
    return 'sha256=' + hmac.new(geheimnis.encode('utf-8'), rumpf, hashlib.sha256).hexdigest()


RUMPF = json.dumps({'ref': 'refs/heads/main'}).encode('utf-8')


@pytest.mark.asyncio
@pytest.mark.parametrize('fehlendes_geheimnis', ['', None, '   '])
async def test_ohne_konfiguriertes_geheimnis_wird_nichts_verarbeitet(fehlendes_geheimnis):
    """Fehlt das Secret, muss die Route sperren — nicht durchwinken."""
    integration = _integration(fehlendes_geheimnis)

    antwort = await integration.webhook_handler(_anfrage(RUMPF, _signiere(RUMPF, 'egal')))

    assert antwort.status == 503
    integration.event_handlers['push'].assert_not_awaited()
    assert integration.pending_webhooks == []


@pytest.mark.asyncio
async def test_ohne_geheimnis_wird_auch_nicht_in_die_warteschlange_gelegt():
    """Auch der Startup-Puffer darf keine ungeprüfte Nutzlast aufnehmen."""
    integration = _integration('')
    integration.bot_ready = False

    antwort = await integration.webhook_handler(_anfrage(RUMPF))

    assert antwort.status == 503
    assert integration.pending_webhooks == []


@pytest.mark.asyncio
async def test_fehlende_signatur_wird_abgewiesen():
    integration = _integration('geheim')

    antwort = await integration.webhook_handler(_anfrage(RUMPF, signatur=None))

    assert antwort.status == 401
    integration.event_handlers['push'].assert_not_awaited()


@pytest.mark.asyncio
async def test_falsche_signatur_wird_abgewiesen():
    integration = _integration('geheim')

    antwort = await integration.webhook_handler(_anfrage(RUMPF, _signiere(RUMPF, 'falsch')))

    assert antwort.status == 401
    integration.event_handlers['push'].assert_not_awaited()


@pytest.mark.asyncio
async def test_gueltig_signiertes_ereignis_bleibt_funktionsfaehig():
    integration = _integration('geheim')

    antwort = await integration.webhook_handler(_anfrage(RUMPF, _signiere(RUMPF, 'geheim')))

    assert antwort.status == 200
    integration.event_handlers['push'].assert_awaited_once()


@pytest.mark.asyncio
async def test_health_endpunkt_meldet_fehlendes_geheimnis():
    """Eine stumme Sperre ist die halbe Lösung: Ein fehlendes Secret legt die
    Deploy-Kette still. Ohne sichtbares Merkmal sucht der Betreiber den Fehler
    beim Deploy statt bei der Konfiguration."""
    integration = _integration('')

    antwort = await integration.health_check(Mock())
    daten = json.loads(antwort.text)

    assert daten['status'] == 'degraded'
    assert daten['webhook_secret_configured'] is False


@pytest.mark.asyncio
async def test_health_endpunkt_bleibt_gesund_mit_geheimnis():
    integration = _integration('geheim')

    antwort = await integration.health_check(Mock())
    daten = json.loads(antwort.text)

    assert daten['status'] == 'healthy'
    assert daten['webhook_secret_configured'] is True
