"""
Bind-Hosts für interne HTTP-Server: 127.0.0.1 + Docker-Bridge statt 0.0.0.0.

Befund ZERODOX#3212: `HealthCheckServer` (Port 8766) und
`GuildScoutAlertsHandler` (Port 9091) banden auf `0.0.0.0`, obwohl der
einzige dokumentierte Grund die Docker-Bridge `172.17.0.1` war. `bind_hosts()`
liefert die engstmögliche Liste; diese Tests prüfen die Ermittlung selbst und
dass die beiden Server sie tatsächlich verwenden (kein `0.0.0.0` mehr an
`web.TCPSite`).
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.utils.bind_hosts import DOCKER_BRIDGE_HOST, ENV_OVERRIDE, LOCALHOST, bind_hosts
from src.utils.health_server import HealthCheckServer


# --- bind_hosts() selbst ---


def test_enthaelt_immer_127_0_0_1(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    assert LOCALHOST in bind_hosts()


def test_liefert_niemals_0_0_0_0(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    assert "0.0.0.0" not in bind_hosts()


def test_docker_bridge_wird_aufgenommen_wenn_verfuegbar(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts._adresse_verfuegbar", return_value=True):
        hosts = bind_hosts()
    assert hosts == [LOCALHOST, DOCKER_BRIDGE_HOST]


def test_fehlende_docker_bridge_faellt_auf_localhost_zurueck_und_warnt(monkeypatch, caplog):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    with patch("src.utils.bind_hosts._adresse_verfuegbar", return_value=False):
        with caplog.at_level("WARNING", logger="shadowops.bind_hosts"):
            hosts = bind_hosts()
    assert hosts == [LOCALHOST]
    assert any("172.17.0.1" in satz.message for satz in caplog.records)


def test_env_override_wird_respektiert(monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, "10.0.0.5, 10.0.0.6")
    with patch("src.utils.bind_hosts._adresse_verfuegbar", return_value=True) as verfuegbar:
        hosts = bind_hosts()
    assert hosts == ["10.0.0.5", "10.0.0.6"]
    verfuegbar.assert_not_called()


def test_env_override_leer_wird_ignoriert(monkeypatch):
    monkeypatch.setenv(ENV_OVERRIDE, "   ")
    with patch("src.utils.bind_hosts._adresse_verfuegbar", return_value=True):
        hosts = bind_hosts()
    assert hosts == [LOCALHOST, DOCKER_BRIDGE_HOST]


# --- HealthCheckServer.start() verwendet bind_hosts() statt 0.0.0.0 ---


@pytest.mark.asyncio
async def test_health_server_bindet_nicht_mehr_auf_0_0_0_0(monkeypatch):
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)

    bot = Mock()
    server = HealthCheckServer(bot, port=8766)

    fake_runner = MagicMock()
    fake_runner.setup = AsyncMock()
    fake_site = MagicMock()
    fake_site.start = AsyncMock()

    aufrufe = []

    def _tcpsite(*args, **kwargs):
        aufrufe.append((args, kwargs))
        return fake_site

    with patch("src.utils.health_server.web.AppRunner", return_value=fake_runner), \
         patch("src.utils.health_server.web.TCPSite", side_effect=_tcpsite) as tcpsite_mock, \
         patch("src.utils.bind_hosts._adresse_verfuegbar", return_value=True):
        await server.start()

    assert tcpsite_mock.call_count == 1
    args, kwargs = aufrufe[0]
    uebergebene_hosts = kwargs.get("host", args[1] if len(args) > 1 else None)

    assert uebergebene_hosts != "0.0.0.0"
    assert "0.0.0.0" not in (uebergebene_hosts or [])
    assert LOCALHOST in uebergebene_hosts
